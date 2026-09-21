# kb-loom-flux2-weights — the FLUX.2-dev weights spike: off the Comfy repackaging

*Started 2026-09-21 by Claude Code at the author's request, after the M2.17 cache work; the CPU half measured the same day (§4, 15:45 CEDT): "Comfy-Org/flux2-dev is a ComfyUI-compatible quantized model; it should be replaced with something official, like unsloth's GGUF. Can we create a spike for this?" A spike, not a build: the facts that can be settled without the card are settled here (the candidates, their formats, what loom's own ggml path does with them on the CPU); the card decides the rest. Related: M2.15 CPU / ggml spike (journal "🧪 CPU / ggml spike"), M2.16 sdcpp adapter (spec §12 3j), M2.17 model cache (kb-loom-cache.md).*

---

## 0. The dilemma

loom's `flux.2-dev` runs on files from **`Comfy-Org/flux2-dev`** (M2.5, chosen to escape the gated BFL and Mistral repos): a *scaled-FP8 mixed* transformer, a Mistral text encoder in Comfy's key layout, the VAE, and a community Turbo LoRA. It works, but every piece needs loom-side plumbing that exists only for that repackaging (`pipelines/multistack/src/pipeline/flux2/scaled_fp8.py`: `ScaledFP8Linear`, three key remaps, LoRA row-slicing over fused qkv, a vendored Mistral config), and the repo's own metadata names its base model as a community Turbo finetune. The author wants the bytes to come from a source with clean provenance in a standard format.

What "official" can mean here, precisely:

| source | what it is | gated | format |
| --- | --- | --- | --- |
| `black-forest-labs/FLUX.2-dev` | **the** weights: bf16 transformer 64.5 GB (single file + diffusers shards), Mistral-Small-3.2 bf16 48 GB, `ae.safetensors` 0.34 GB; 178 GB in all | yes (auto-approve on the license; **the author's token downloads it** — verified today with `ae.safetensors`) | safetensors, BFL + diffusers layouts |
| `unsloth/FLUX.2-dev-GGUF` | third-party quantizations of the BFL transformer, tagged `base_model:quantized:black-forest-labs/FLUX.2-dev`; Q2_K 12.9 · Q3_K_M 15.8 · Q4_K_S 19.8 · **Q4_K_M 20.0** · Q5_K_M 23.9 · Q6_K 27.4 · Q8_0 35.0 · BF16 64.5 GB; transformer only | no | GGUF (the llama.cpp / sd.cpp / ComfyUI-GGUF standard) |
| `city96/FLUX.2-dev-gguf` | the same quant set by the ComfyUI-GGUF author (the one sd.cpp's docs name); sizes within 1 % of unsloth's | no | GGUF |
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | the text encoder's official weights, bf16 48 GB | **no** (the catalog's "gated Mistral" note is out of date) | safetensors |
| `unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF` | quantizations of that: **Q4_K_M 14.3** · Q5_K_M 16.8 · Q6_K 19.4 · Q8_0 25.1 GB — the file sd.cpp's docs pass to `--llm` | no | GGUF |
| `black-forest-labs/FLUX.2-small-decoder` | BFL's own alternative VAE: the full encoder with a smaller decoder, 0.25 GB | no | safetensors |
| `Comfy-Org/flux2-dev` (today) | scaled-FP8 transformer 35.5 GB · Mistral fp8 18.0 / bf16 35.6 / fp4 12.3 GB · VAE 0.34 GB (byte-identical to BFL's `ae.safetensors`, verified in M2.5) · Turbo LoRA 2.76 GB | no | safetensors in Comfy's layouts |

So: unsloth's GGUF is not "official" (nothing quantized is), but it is a **standard format of the official weights with declared provenance**, and the two other pieces have official, now-ungated sources. The Turbo LoRA has no official counterpart; it stays a community file or is dropped.

## 1. What was read today (no torch, no card)

- **The GGUF header of `flux2-dev-Q4_K_M.gguf`** (one 6 MB range request, `tools/gguf/inspect_gguf.py`): GGUF v3, `general.architecture = flux`, 299 tensors under **the BFL names loom's vendored model already uses** (`double_blocks.N.img_attn.qkv.weight`, `single_blocks.N…`, `img_in`, `txt_in`, `time_in`, `guidance_in`, `final_layer`, the three modulation tensors) — no remap; mixed precision: 124 tensors Q4_K (14.7 GB) + 36 Q5_K (3.6 GB, the double-block attention and MLP weights) + 11 BF16 (1.6 GB, the in/out and modulation layers) + 128 F32 norms.
- **The Mistral Q4_K_M GGUF header** (12 MB range request; the header alone is 7.9 MB of tokenizer): `general.architecture = llama`, 40 blocks in llama.cpp's layout (`token_embd`, `blk.N.attn_q/k/v/output`, `ffn_*`, `output_norm`), 241 × Q4_K + 41 × Q6_K + 81 × F32, 14.33 GB — the layout sd.cpp's FLUX.2 conditioner reads for `--llm`.
- **The hub link today: 15 MB/s** (180 MB in 12 s), not the 1 MB/s of 2026-09-20 that stopped the CPU spike's GGUF download at 2.6 GB. A 20 GB quant is ~25 minutes.
- **sd.cpp (the staged `master-881-17860c0` build)** already has the whole standard-format recipe in its own docs: `--diffusion-model <flux2-dev-Q4_K_S.gguf> --vae <BFL ae.safetensors> --llm <Mistral-Small-3.2 Q4_K_M.gguf>`, plus `--offload-to-cpu` and `--backend diffusion=vulkan0,clip=cpu,vae=cpu` for a card that cannot hold everything.
- **diffusers 0.39.0.dev0** in the venv has `Flux2Transformer2DModel` + a single-file converter for the BFL layout and a GGUF quantizer (`quantizers/gguf/utils.py`: Q2_K … Q8_0 dequantized in pure torch, CUDA kernels optional) — the building blocks of a torch path exist; the `gguf` package itself is not installed.
- **The user's token opens the BFL repo**: `ae.safetensors` fetched from `black-forest-labs/FLUX.2-dev` at 14:23 (so the official VAE is in the cache now, next to the Comfy copy).

## 2. Two ways off the Comfy files

**Path G — the ggml backend (M2.16's `sdcpp` adapter).** The standard stack as sd.cpp documents it. Nothing Comfy-specific remains for dev: GGUF transformer + GGUF Mistral + BFL VAE. Today on the CPU (measured below); on the card through the staged Vulkan build or a HIP build, with the text encoder and VAE on the CPU and the transformer on the card. VRAM on the RX 9070 XT (16 GB): Q2_K 12.9 and Q3_K_M 15.8 GB fit the transformer alone; Q4_K_M (20.0 GB) needs `--offload-to-cpu` streaming — versus today's torch path, which pages a 35.5 GB FP8 transformer and an 18 GB FP8 encoder through the same 16 GB at ≥20 s/step (the M2.9 probe; dev-turbo@4 steps ≈ 100 s per cell).

**Path T — the torch pipeline (the vendored BFL `Flux2`).** Replace `ScaledFP8Linear` with a GGUF-backed Linear (diffusers' `GGUFParameter` / dequantize-per-matmul pattern) fed by the GGUF tensors — the header shows the names match, so no remap; the Turbo LoRA hooks already wrap Linears. The text encoder is the catch: transformers 5.x loads a GGUF only by dequantizing to bf16 at load (48 GB, not for a 16 GB card), and its FP8 paths want CUDA kernels — so on ROCm the Comfy FP8 Mistral would stay. Path T removes Comfy from the transformer only, costs 2–3 days, and cannot be validated until the card is back. It is the fallback if G disappoints on the card.

## 3. Questions, and who answers them

| # | question | answered by |
| --- | --- | --- |
| Q1 | does the standard stack run in loom's ggml path at all? | today, CPU |
| Q2 | time and RAM against the Comfy-FP8 baseline (1 651 s / 50.6 GB at 512², 20 steps, Q8-at-load; per-step ≈ 80 s) | today, CPU |
| Q3 | quality at Q4_K_M vs the baseline — same prompt, seed, sampler, side by side | today, CPU (the two PNGs) |
| Q4 | does the Mistral Q4_K_M GGUF condition as well as the bf16 Comfy encoder — the M2.15 JSON prompt again | today, CPU |
| Q5 | on the card: Q3_K_M vs Q4_K_M with `--offload-to-cpu`, s/step vs the torch FP8 paged path | rig |
| Q6 | the Turbo LoRA on a GGUF base in sd.cpp (`--lora-model-dir`), 4–8 steps | rig |
| Q7 | Path T, only if G disappoints on the card | rig |

## 4. Measured today (CPU, 32 threads, 512², same prompt and seed as M2.15)

| run | steps | text encode | sampling | total | RAM | image |
| --- | --- | --- | --- | --- | --- | --- |
| Comfy fp8 transformer + bf16 Mistral, Q8 at load (M2.15 baseline) | 20 | 46.7 s | 1 599 s | **1 651 s** | 50.6 GB | `.tmp/sdcpp/bench/flux2dev_512_20_q8.png` (JSON prompt: `…_q8_json.png`) |
| **unsloth Q4_K_M + Mistral Q4_K_M GGUF + BFL ae** | 20 | 3.4 (JSON prompt 6.6) s | 1 364 (JSON 1 354) s | **1 373 (JSON 1 366) s** | 32.3 GB | `.tmp/sdcpp/bench/flux2dev-gguf_512_20.png` |

**Verdict (Q1–Q3):** Q1 yes: the standard stack runs unchanged in loom's ggml path (sd.cpp reads the GGUF transformer, the llama-layout Mistral GGUF and BFL's ae with no loom-side plumbing). Q2 faster and lighter than the Comfy files: text encode 3.4 s vs 46.7 s (14×), sampling −15 % (68 s/step vs 80), total −17 %, RAM 32.3 GB vs 50.6 GB (−36 %: encoder 13.1 + transformer 19.0 + VAE 0.16). Q3 no visible quantization cost at Q4_K_M: the plain-prompt image keeps the composition (ranger, cloak, path, dawn through pines) with a more weathered face and natural hands, no artefacts.

**Q4 (the JSON prompt through the Mistral GGUF):** the same directives followed as with the 35 GB bf16 Comfy encoder — the over-the-shoulder look, the hand on the sword hilt at the hip, the brass compass and the leather satchel (both clearer than in the baseline), mist between pines, rim light from behind, the emerald / rust / cream palette; the same soft miss (the low angle reads as eye level); encode 6.6 s vs 45 s, sampling 1 354 s. The 13 GB Mistral Q4_K_M GGUF conditions dev as well as the bf16 file.

Run it: `.\tools\sdcpp\bench-cpu.ps1 flux2dev-gguf -Steps 20` (`-Quant Q3_K_M`, `-LlmQuant Q6_K` to vary).

## 5. On the rig (the author, when the card is back)

1. Unpack the staged Vulkan build (`.tmp/sdcpp/vulkan/`), then `bench-cpu.ps1 flux2dev-gguf` with `LOOM_SDCPP_BIN` pointing at it and `--backend diffusion=vulkan0,clip=cpu,vae=cpu` (add the flag to the helper when the time comes): Q3_K_M first (fits), then Q4_K_M with `--offload-to-cpu`. Record s/step against the torch FP8 path's ≥20 s/step and the dev-turbo 100 s/cell.
2. The same two prompts through the torch path at the same seed for a side-by-side (seeds do not match across backends; judge adherence and artefacts, not pixels).
3. The Turbo LoRA over the GGUF base (`--lora-model-dir`, 4–8 steps).
4. Decide D1–D6 below.

## 6. Decisions for the author

| # | question | recommendation |
| --- | --- | --- |
| D1 | Where dev leaves the Comfy files first | **on the ggml backend** (Path G) as M2.16's first deliverable: the roster gains `unsloth/FLUX.2-dev-GGUF` (one quant), `unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF` (one quant) and `black-forest-labs/FLUX.2-dev` `ae.safetensors`; the torch path keeps the Comfy FP8 files until the rig compares (Q5), then either drops them or Path T follows |
| D2 | unsloth vs city96 | unsloth (the author's pick; same format and names, provenance tag present, two weeks newer); city96 is the drop-in alternate |
| D3 | The VAE | BFL's `ae.safetensors` through the token (it is in the cache now; byte-identical to the Comfy copy, so nothing changes visually); the ungated small-decoder is a different decoder, not a substitute |
| D4 | The text-encoder quant | start at **Q4_K_M** (14.3 GB, what sd.cpp documents); go to Q6_K (19.4 GB) only if Q4 loses prompt adherence (Q4 in §4) |
| D5 | The transformer quant | **Q4_K_M** on the CPU (quality/RAM) and for a card with offload; **Q3_K_M** if the card must hold it whole; Q8_0 only for A/B reference |
| D6 | The Turbo LoRA | keep the Comfy file as an *optional* accelerator in the roster (it has no official source), tested on the GGUF base in Q6; drop it if sd.cpp cannot apply it there |

## 7. Acceptance of the spike

Q1–Q4 answered in §4 with the two images; Q5–Q6 answered on the rig; D1–D6 decided; the M2.16 roster/catalog entries written from D1–D5. The spike does not change what loom runs today.
