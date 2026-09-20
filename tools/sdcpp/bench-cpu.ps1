<#
.SYNOPSIS
  Run one of loom's image families on the CPU through stable-diffusion.cpp (the 2026-09-20 spike).

.DESCRIPTION
  Resolves every weight from the existing HF cache (F:\HF_HOME) + the local merges, builds the
  sd-cli command line the spike measured with, runs it in the foreground and prints the timing
  lines. Defaults = the spike's operating point: 512², 32 threads, flash attention, Q8_0 at load.

  Prerequisites (one-off):
    * the sd.cpp CPU build unpacked at $env:LOOM_SDCPP_BIN's folder or .tmp\sdcpp\cpu\sd-cli.exe
      (release master-881-17860c0, `sd-master-17860c0-bin-win-cpu-x64.zip`)
    * klein / zimage text encoder: merge the cached Qwen3-4B shards once —
        python tools\sdcpp\merge_safetensors.py F:\HF_HOME\hub\models--Qwen--Qwen3-4B\snapshots\<rev> .tmp\sdcpp\models\qwen_3_4b.safetensors
    * zimage: the Comfy/original single file or a GGUF (e.g. leejet/Z-Image-Turbo-GGUF Q8_0) + Comfy-Org/z_image_turbo split_files/vae/ae.safetensors

.PARAMETER Model   sd35 | klein4b | flux2dev | zimage
.PARAMETER Steps   default per model (sd35 28 · klein4b 4 · flux2dev 20 · zimage 8)
.PARAMETER Size    square size in px (default 512)
.PARAMETER Type    weight type at load: q8_0 (default) | bf16 (as stored) | q4_k …
.PARAMETER Threads default 32
.PARAMETER Init    optional init image (img2img) — pair with -Strength
.PARAMETER Out     output png (default .tmp\sdcpp\bench\<model>_<size>_<steps>.png)

.EXAMPLE
  .\tools\sdcpp\bench-cpu.ps1 klein4b
  .\tools\sdcpp\bench-cpu.ps1 sd35 -Init .\base.png -Strength 0.5
  .\tools\sdcpp\bench-cpu.ps1 flux2dev -Prompt "..." -Steps 8
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("sd35", "klein4b", "flux2dev", "zimage")] [string]$Model,
    [int]$Steps = 0,
    [int]$Size = 512,
    [string]$Type = "q8_0",
    [int]$Threads = 32,
    [string]$Prompt = "a weathered female ranger in a green hooded cloak standing on a forest path at dawn, portrait, cinematic lighting, sharp focus",
    [string]$Init = "",
    [double]$Strength = 0.5,
    [int]$Seed = 42,
    [string]$Out = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path          # loom-loreweave-studio
$mono = (Resolve-Path (Join-Path $repo "..\..")).Path                  # stubz-002-tripo-sf
$hf = if ($env:HF_HOME) { $env:HF_HOME } else { "F:\HF_HOME" }
$exe = if ($env:LOOM_SDCPP_BIN) { $env:LOOM_SDCPP_BIN } else { Join-Path $mono ".tmp\sdcpp\cpu\sd-cli.exe" }
if (-not (Test-Path -LiteralPath $exe)) { throw "sd-cli.exe not found at $exe — unpack the sd.cpp CPU build there or set LOOM_SDCPP_BIN" }
$models = Join-Path $mono ".tmp\sdcpp\models"

function Snap([string]$repoId) {
    $d = Join-Path $hf ("hub\models--" + ($repoId -replace "/", "--") + "\snapshots")
    $s = Get-ChildItem -LiteralPath $d -Directory | Select-Object -First 1
    if (-not $s) { throw "not cached: $repoId" }
    return $s.FullName
}
function Need([string]$p) { if (-not (Test-Path -LiteralPath $p)) { throw "missing weight file: $p" }; return $p }

switch ($Model) {
    "sd35" {
        $s = Snap "stabilityai/stable-diffusion-3.5-medium"
        $files = @("--diffusion-model", (Need "$s\sd3.5_medium.safetensors"),
                   "--vae", (Need "$s\vae\diffusion_pytorch_model.safetensors"),   # the single file holds only the VAE decoder
                   "--clip_l", (Need "$s\text_encoders\clip_l.safetensors"),
                   "--clip_g", (Need "$s\text_encoders\clip_g.safetensors"),
                   "--t5xxl", (Need "$s\text_encoders\t5xxl_fp16.safetensors"))
        $gen = @("--cfg-scale", "4.5"); if ($Steps -eq 0) { $Steps = 28 }
    }
    "klein4b" {
        $files = @("--diffusion-model", (Need ((Snap "black-forest-labs/FLUX.2-klein-4B") + "\flux-2-klein-4b.safetensors")),
                   "--vae", (Need ((Snap "Comfy-Org/flux2-dev") + "\split_files\vae\flux2-vae.safetensors")),
                   "--llm", (Need "$models\qwen_3_4b.safetensors"))
        $gen = @("--cfg-scale", "1.0"); if ($Steps -eq 0) { $Steps = 4 }
    }
    "flux2dev" {
        $s = Snap "Comfy-Org/flux2-dev"
        $files = @("--diffusion-model", (Need "$s\split_files\diffusion_models\flux2_dev_fp8mixed.safetensors"),   # scaled fp8, read directly
                   "--vae", (Need "$s\split_files\vae\flux2-vae.safetensors"),
                   "--llm", (Need "$s\split_files\text_encoders\mistral_3_small_flux2_bf16.safetensors"))
        $gen = @("--cfg-scale", "1.0", "--guidance", "3.0"); if ($Steps -eq 0) { $Steps = 20 }
    }
    "zimage" {
        $gguf = Get-ChildItem -LiteralPath (Join-Path $hf "hub\models--leejet--Z-Image-Turbo-GGUF\snapshots") -Recurse -Filter "*Q8_0*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
        $dm = if ($gguf) { $gguf.FullName } else { "$models\z_image_turbo_bf16.safetensors" }
        $files = @("--diffusion-model", (Need $dm),
                   "--vae", (Need ((Snap "Comfy-Org/z_image_turbo") + "\split_files\vae\ae.safetensors")),
                   "--llm", (Need "$models\qwen_3_4b.safetensors"))
        $gen = @("--cfg-scale", "1.0"); if ($Steps -eq 0) { $Steps = 8 }
    }
}
if (-not $Out) { $Out = Join-Path $mono ".tmp\sdcpp\bench\${Model}_${Size}_${Steps}.png" }
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$argv = @("-M", "img_gen") + $files + @("-p", $Prompt, "-W", "$Size", "-H", "$Size", "--steps", "$Steps", "--seed", "$Seed",
         "-t", "$Threads", "--diffusion-fa", "-v", "-o", $Out) + $gen
if ($Type -and $Type -ne "bf16") { $argv += @("--type", $Type) }
if ($Init) { $argv += @("-i", $Init, "--strength", "$Strength") }

Write-Host "[bench-cpu] $Model · ${Size}² · $Steps steps · $Type · $Threads threads → $Out" -ForegroundColor DarkYellow
$t0 = Get-Date
& $exe @argv 2>&1 | ForEach-Object {
    $line = "$_"
    if ($line -match 'total params memory|get_learned_condition completed|sampling completed|decode_first_stage completed|generate_image completed|ERROR') { Write-Host $line.Trim() }
}
Write-Host ("[bench-cpu] wall {0:N0} s → {1}" -f ((Get-Date) - $t0).TotalSeconds, $Out) -ForegroundColor DarkYellow
