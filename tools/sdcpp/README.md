# `tools/sdcpp` — the CPU / ggml spike (2026-09-20)

Runs loom's image families on the **CPU** through [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)
(ggml), with no torch and no GPU — measured on the Ryzen 9 9950X while the RX 9070 XT is away on RMA.
Results and the adapter design: journal `.docs/kb-loom-p2-imp.md` "🧪 CPU/ggml spike".

## One-off setup

1. **Binary.** Download `sd-master-17860c0-bin-win-cpu-x64.zip` (release `master-881-17860c0`) and
   unpack it to `<monorepo>/.tmp/sdcpp/cpu/` (so `sd-cli.exe` sits there), or point
   `LOOM_SDCPP_BIN` at `sd-cli.exe`. The same release's `…-win-vulkan-x64.zip` is the ROCm-free
   GPU path for when the card is back (`.tmp/sdcpp/vulkan/`). ggml picks the CPU kernels at
   runtime (this machine: `ggml-cpu-cascadelake` = AVX-512 + VNNI).
2. **Text encoder for klein / Z-Image** — built from the cache, no download:
   ```powershell
   ..\..\.venv\Scripts\python.exe tools\sdcpp\merge_safetensors.py `
     F:\HF_HOME\hub\models--Qwen--Qwen3-4B\snapshots\<rev> ..\..\.tmp\sdcpp\models\qwen_3_4b.safetensors
   ```
3. **Z-Image only:** the runtime needs the Comfy/original single file or a GGUF (the merged
   diffusers transformer is refused — split q/k/v vs fused qkv). `leejet/Z-Image-Turbo-GGUF`
   `z_image_turbo-Q8_0.gguf` (6.7 GB) + `Comfy-Org/z_image_turbo` `split_files/vae/ae.safetensors`.

Everything else (SD3.5 Medium + its three text encoders + diffusers VAE, klein-4B, the Comfy
scaled-fp8 FLUX.2-dev transformer + bf16 Mistral, the flux2 VAE) is read straight from the cache.

## Run

```powershell
.\tools\sdcpp\bench-cpu.ps1 klein4b                      # 512², 4 steps, Q8 at load → ~1 min
.\tools\sdcpp\bench-cpu.ps1 sd35                         # 512², 28 steps CFG → ~2 min
.\tools\sdcpp\bench-cpu.ps1 sd35 -Init base.png -Strength 0.5   # a "Clean" pass → ~1.2 min
.\tools\sdcpp\bench-cpu.ps1 flux2dev -Steps 20           # 512² → ~27 min, 50 GB RAM
.\tools\sdcpp\bench-cpu.ps1 zimage                       # once the GGUF is in
```
