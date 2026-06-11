"""Multi-ControlNet inpainting pipeline for SD 3.5.

`StableDiffusion3ControlNetInpaintingPipeline` (the alimama-merged class in
diffusers core) supports a list of ControlNets, but its multi-CN code path
unconditionally calls `prepare_image_with_mask` for every control image --
which VAE-encodes the image, adds a binary mask channel, and produces a
17-channel latent-resolution tensor. That format is correct for the alimama
inpaint CN (`extra_conditioning_channels=1`) but wrong for any other SD 3
CN (e.g. InstantX Depth/Canny/Pose, all `extra_conditioning_channels=0`),
which expects a 16-channel VAE-encoded latent with no mask channel.

This subclass keeps the alimama image+mask+control_mask plumbing for
inpaint-CNs, and falls back to the standard SD 3 CN preparation
(VAE-encode the conditioning image, scale-shift, no mask channel) for
non-inpaint CNs. Dispatch is driven by each CN's
`extra_conditioning_channels` config value, so this works for any future
SD 3 CN without code changes.

Used by `pipeline.sd35` mode `cn-inpaint-mc` (e.g. alimama inpaint-CN +
InstantX depth-CN side by side -- scene context + hand anatomy guidance
in one inpaint pass).
"""

from __future__ import annotations

import torch
from diffusers import (
    SD3ControlNetModel,
    SD3MultiControlNetModel,
    StableDiffusion3ControlNetInpaintingPipeline,
)


class SD3MultiControlNetInpaintPipeline(StableDiffusion3ControlNetInpaintingPipeline):
    """Drop-in subclass that dispatches `prepare_image_with_mask` per-CN.

    Single-CN behaviour is unchanged (super().__call__ falls through to the
    parent's single-CN branch). Multi-CN behaviour: for each CN, decide
    whether to pack the mask channel based on
    `cn.config.extra_conditioning_channels`.

    Mechanism: the parent's __call__ iterates `for control_image_ in
    control_image` and calls `self.prepare_image_with_mask(...)` once per
    CN, in CN order. We track which CN we're on with a per-call counter
    `_mc_call_idx`, set to 0 in our __call__ wrapper and incremented on
    each invocation of our overridden prep method.
    """

    # Standard 3-channel CN prep -- copied from
    # StableDiffusion3ControlNetPipeline.prepare_image to avoid an import-time
    # dependency on that class. Functionally identical: preprocess the image,
    # repeat for the batch, optionally double for CFG.
    def _prep_image_no_mask(
        self,
        image,
        width,
        height,
        batch_size,
        num_images_per_prompt,
        device,
        dtype,
        do_classifier_free_guidance=False,
        guess_mode=False,
    ):
        if isinstance(image, torch.Tensor):
            pass
        else:
            image = self.image_processor.preprocess(image, height=height, width=width)
        image_batch_size = image.shape[0]
        repeat_by = batch_size if image_batch_size == 1 else num_images_per_prompt
        image = image.repeat_interleave(repeat_by, dim=0)
        image = image.to(device=device, dtype=dtype)
        # VAE-encode to match the latent-space input format the SD 3 CN expects
        # (in_channels=16). Same scale/shift as the standard CN pipeline.
        image = self.vae.encode(image).latent_dist.sample()
        image = (image - self.vae.config.shift_factor) * self.vae.config.scaling_factor
        image = image.to(dtype)
        if do_classifier_free_guidance and not guess_mode:
            image = torch.cat([image] * 2)
        return image

    def prepare_image_with_mask(self, image, mask, *args, **kwargs):
        """Override that dispatches per-CN in multi-CN runs.

        For single-CN runs (or when no `_mc_call_idx` is set), delegates to
        the parent's alimama prep -- behaviour unchanged for the standard
        single inpaint-CN use case.

        For multi-CN runs we look up the current CN by index, peek at its
        `extra_conditioning_channels`, and either pack the mask (alimama
        style, channel count > 0) or do plain image prep (InstantX style,
        no mask channel).
        """
        idx = getattr(self, "_mc_call_idx", None)
        is_multi = isinstance(self.controlnet, SD3MultiControlNetModel)
        if idx is None or not is_multi:
            return super().prepare_image_with_mask(image, mask, *args, **kwargs)

        cn = self.controlnet.nets[idx]
        # Bump the counter so the next call lands on the next CN.
        self._mc_call_idx = idx + 1

        extra = int(getattr(cn.config, "extra_conditioning_channels", 0) or 0)
        if extra > 0:
            return super().prepare_image_with_mask(image, mask, *args, **kwargs)

        # Plain image prep -- no mask channel. Drop `mask` since
        # `_prep_image_no_mask` doesn't take it.
        return self._prep_image_no_mask(image, *args, **kwargs)

    def __call__(self, *args, **kwargs):
        # Reset the per-CN counter at the start of every call so re-use of
        # the pipeline across multiple generations stays deterministic.
        self._mc_call_idx = 0
        try:
            return super().__call__(*args, **kwargs)
        finally:
            self._mc_call_idx = None
