# Vendored Mistral text-encoder config + tokenizer (flux.2-dev quantized path)

These ~17 MB of **config + tokenizer only** (no weights) are vendored so the quantized
`flux.2-dev` runtime (Comfy-Org split files) can build its Mistral text encoder **without a
dependency on the gated `black-forest-labs/FLUX.2-dev` repo** (spec: `kb-loom-p2.md` §12 "M2.5",
"Dependency elimination"). The transformer/text-encoder/VAE **weights** come from
`Comfy-Org/flux2-dev` split files; only the architecture `config.json` and the tokenizer are needed
from BFL, and they are loaded from here via a local path.

| File | From `black-forest-labs/FLUX.2-dev` | Used by |
| --- | --- | --- |
| `text_encoder/config.json` | `text_encoder/config.json` | `AutoConfig.from_pretrained(<here>, subfolder="text_encoder")` (builds `Mistral3Config`) |
| `text_encoder/generation_config.json` | same | parity only (not read at inference) |
| `tokenizer/*` | `tokenizer/*` | `AutoProcessor.from_pretrained(<here>, subfolder="tokenizer")` (`PixtralProcessor`) |

**Provenance / license:** copied from the gated `black-forest-labs/FLUX.2-dev` HF repo. Vendored for
this private project (licensing deferred). If this repo is ever made public, move these out of git
(fetch from the companion weights repo as small `file` entries) rather than redistributing them here.

Loaded by `pipeline/flux2/scaled_fp8.py` (`load_comfy_mistral_text_encoder`, default
`config_repo`/`processor_repo` = this directory).
