"""Multi-pipeline orchestrator CLI.

Subcommands (v2 -- see kb-multi-image2.md):

  - batch             -- ideate (3xN) -> [--clean] -> [--polish (P4)]
                         A run is a *batch*; the whole pool is the deliverable.
  - ideate            -- candidate-pool generation only (unchanged)
  - clean             -- standalone Z-Image-Base img2img on one image
  - diversity-grid    -- Architecture A.0: 1 prompt x 3 pipelines side-by-side
  - lineage           -- walk session manifests

Removed in v2 (emit a migration error): `compose-character`, `select`.

Every subcommand supports `--session-id` and `--continue-from RUN_ID` for
session attachment + branching.
"""

from __future__ import annotations

import argparse
import random
import sys

from .arch_batch import run_batch
from .arch_compose_character import CLEAN_DEFAULTS, run_ideate_only, run_clean_only
from .arch_diversity_grid import run_diversity_grid
from .lineage import render_artifact_lookup, render_json, render_tree


# --- Common argument groups ------------------------------------------------


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--output-dir", default="src/assets/pics",
                   help="Where the final output + multi-image manifest are written")
    p.add_argument("--intermediate-root", default="src/assets/pics/intermediate",
                   help="Parent directory for per-run intermediate dirs")
    p.add_argument("--sessions-dir", default="src/state/sessions",
                   help="Where session manifests live (default: src/state/sessions/)")
    p.add_argument("--session-id", default=None,
                   help="Attach to an existing session by id. If omitted and "
                        "--continue-from is also omitted, a new session is minted.")
    p.add_argument("--continue-from", dest="continue_from", default=None,
                   help="Fork from a previous multi-image run by run_id. "
                        "Inherits the session_id of that run.")
    p.add_argument("--keep-intermediates", action="store_true",
                   help="Preserve the intermediate dir after a successful run "
                        "(default for stage-only subcommands)")


# --- Opt-in footgun guard --------------------------------------------------
#
# `--clean` / `--polish` are opt-in master switches. Passing only their
# sub-flags (e.g. `--clean-backend`) without the switch silently runs ideate
# alone -- a confusing no-op. These helpers fail loudly instead.

_CLEAN_SUBFLAGS = (
    "--clean-backend", "--clean-model", "--clean-prompt", "--clean-strength",
    "--clean-negative-prompt", "--no-clean-cfg-normalization",
)
_POLISH_SUBFLAGS = (
    "--polish-backend", "--polish-model", "--polish-prompt",
    "--polish-negative-prompt", "--polish-strength", "--polish-seed",
)


def _provided_long_opts(argv) -> set[str]:
    """Long option names explicitly present in argv (handles `--opt=val`)."""
    return {a.split("=", 1)[0] for a in argv if a.startswith("--")}


def _optin_error(provided: set[str]) -> str | None:
    """Message if clean/polish sub-flags were given without their master
    switch, else None. Keeps the stages opt-in but fails loudly on the
    silent-no-op footgun."""
    msgs = []
    if "--clean" not in provided:
        used = sorted(f for f in _CLEAN_SUBFLAGS if f in provided)
        if used:
            msgs.append(f"{', '.join(used)} given but --clean not set")
    if "--polish" not in provided:
        used = sorted(f for f in _POLISH_SUBFLAGS if f in provided)
        if used:
            msgs.append(f"{', '.join(used)} given but --polish not set")
    if not msgs:
        return None
    return ("ERROR: clean/polish settings provided but the stage is not "
            "enabled:\n  - " + "\n  - ".join(msgs) +
            "\nClean/polish are opt-in -- add the bare --clean / --polish "
            "flag to actually run them.")


_BACKEND_NAMES = ("zimage-img2img", "sd35-img2img", "flux2-img2img")


def _looks_like_backend(value: str | None) -> bool:
    """A `--*-model` value that is actually a backend selector."""
    return bool(value) and (value in _BACKEND_NAMES
                            or value.endswith("-img2img"))


def _model_flag_error(args: argparse.Namespace) -> str | None:
    """Catch the common mistake of passing a backend name (e.g.
    `sd35-img2img`) to `--clean-model` / `--polish-model`, which expect a
    model id. Per-pipeline CLIs only reject this after a full ideate has
    already run, wasting GPU time -- so we fail fast here instead."""
    bad = []
    if _looks_like_backend(getattr(args, "clean_model", None)):
        bad.append(f"--clean-model {args.clean_model!r} looks like a backend "
                   f"-- did you mean --clean-backend {args.clean_model}?")
    if _looks_like_backend(getattr(args, "polish_model", None)):
        bad.append(f"--polish-model {args.polish_model!r} looks like a backend "
                   f"-- did you mean --polish-backend {args.polish_model}?")
    if not bad:
        return None
    return ("ERROR: backend name passed to a --*-model flag:\n  - "
            + "\n  - ".join(bad) +
            "\n--*-model expects a model id (e.g. zimage-base / sd3.5-medium "
            "/ flux.2-klein-4b); --*-backend selects the img2img pipeline.")


# --- Subcommand entry points ----------------------------------------------


def _cmd_diversity_grid(args: argparse.Namespace) -> int:
    seed = args.seed if args.seed is not None else random.randrange(2**31)
    manifest = run_diversity_grid(
        prompt=args.prompt, seed=seed,
        width=args.width, height=args.height,
        output_dir=args.output_dir,
        intermediate_root=args.intermediate_root,
        sessions_dir=args.sessions_dir,
        session_id=args.session_id,
        continue_from_run=args.continue_from,
        keep_intermediates=args.keep_intermediates,
        pipelines=args.pipeline,
    )
    stitch = next((s for s in manifest.stages if s.name == "stitch_grid"), None)
    return 0 if (stitch and stitch.status == "completed") else 2


def _cmd_batch(args: argparse.Namespace) -> int:
    err = _optin_error(_provided_long_opts(sys.argv[1:]))
    if err:
        print(err)
        return 2
    err = _model_flag_error(args)
    if err:
        print(err)
        return 2
    seed = args.seed if args.seed is not None else random.randrange(2**31)
    manifest = run_batch(
        prompt=args.prompt, seed=seed,
        num_candidates=args.num_candidates,
        ideation_mode=args.ideation_mode,
        width=args.width, height=args.height,
        do_clean=args.clean,
        clean_backend=args.clean_backend,
        clean_model=args.clean_model,
        clean_strength=args.clean_strength,
        clean_prompt=args.clean_prompt,
        clean_negative_prompt=args.clean_negative_prompt or CLEAN_DEFAULTS["negative_prompt"],
        clean_cfg_normalization=not args.no_clean_cfg_normalization,
        do_polish=args.polish,
        polish_backend=args.polish_backend,
        polish_model=args.polish_model,
        polish_prompt=args.polish_prompt,
        polish_negative_prompt=args.polish_negative_prompt,
        polish_strength=args.polish_strength,
        polish_seed=args.polish_seed,
        img2img_batching=args.img2img_batching,
        output_dir=args.output_dir,
        intermediate_root=args.intermediate_root,
        sessions_dir=args.sessions_dir,
        session_id=args.session_id,
        continue_from_run=args.continue_from,
        # The batch *is* the deliverable (candidate pool + per-candidate
        # clean/polish outputs), so the pool is always preserved regardless
        # of --keep-intermediates. Standalone clean/polish re-runs need it.
        keep_intermediates=True,
    )
    ideate = next((s for s in manifest.stages if s.name == "ideate"), None)
    ok = bool(ideate and ideate.status == "completed"
              and ideate.outputs.get("succeeded", 0) > 0)
    if args.clean:
        clean = next((s for s in manifest.stages if s.name == "clean"), None)
        ok = ok and bool(clean and clean.status == "completed"
                         and clean.outputs.get("succeeded", 0) > 0)
    if args.polish:
        polish = next((s for s in manifest.stages if s.name == "polish"), None)
        ok = ok and bool(polish and polish.status == "completed"
                         and polish.outputs.get("succeeded", 0) > 0)
    return 0 if ok else 2


def _cmd_removed(args: argparse.Namespace) -> int:
    """Stub for subcommands removed in v2 (kb-multi-image2.md §10)."""
    name = getattr(args, "_removed_name", "this subcommand")
    print(f"ERROR: `{name}` was removed in multi-image v2.\n"
          f"  A run is now a batch -- there is no 'select' and no 'compose'.\n"
          f"  Use:  python -m pipeline.multi.run_pipeline batch --prompt ... "
          f"[--clean] [--polish]\n"
          f"  See .github/copilot/kb-multi-image2.md (migration, section 10).")
    return 2


def _cmd_ideate(args: argparse.Namespace) -> int:
    seed = args.seed if args.seed is not None else random.randrange(2**31)
    manifest = run_ideate_only(
        prompt=args.prompt, seed=seed,
        num_candidates=args.num_candidates,
        ideation_mode=args.ideation_mode,
        width=args.width, height=args.height,
        output_dir=args.output_dir,
        intermediate_root=args.intermediate_root,
        sessions_dir=args.sessions_dir,
        session_id=args.session_id,
        continue_from_run=args.continue_from,
        keep_intermediates=True,    # ideate-only always keeps intermediates
    )
    ideate = next((s for s in manifest.stages if s.name == "ideate"), None)
    return 0 if (ideate and ideate.status == "completed" and
                 ideate.outputs.get("succeeded", 0) > 0) else 2


def _cmd_lineage(args: argparse.Namespace) -> int:
    if args.artifact:
        print(render_artifact_lookup(args.artifact, sessions_dir=args.sessions_dir,
                                     fmt=args.format))
        return 0
    if not args.target:
        print("ERROR: provide either a SESSION_ID-or-RUN_ID positional, or --artifact")
        return 2
    # Resolve target -> session_id
    from .lineage import _load_session_data, _resolve_target
    all_sessions = _load_session_data(sessions_dir=args.sessions_dir)
    sid = _resolve_target(args.target, all_sessions)
    if sid is None:
        print(f"ERROR: no session or run with id {args.target!r} found under {args.sessions_dir}")
        return 2
    if args.format == "json":
        print(render_json(sid, sessions_dir=args.sessions_dir))
    else:
        print(render_tree(sid, sessions_dir=args.sessions_dir,
                          show_failed=args.show_failed,
                          depth_limit=args.depth))
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    seed = args.seed if args.seed is not None else random.randrange(2**31)
    manifest = run_clean_only(
        input_image=args.input_image,
        prompt=args.prompt,
        seed=seed,
        strength=args.strength,
        negative_prompt=args.negative_prompt or CLEAN_DEFAULTS["negative_prompt"],
        cfg_normalization=not args.no_cfg_normalization,
        width=args.width, height=args.height,
        output_dir=args.output_dir,
        intermediate_root=args.intermediate_root,
        sessions_dir=args.sessions_dir,
        session_id=args.session_id,
        continue_from_run=args.continue_from,
        keep_intermediates=True,
    )
    clean = next((s for s in manifest.stages if s.name == "clean"), None)
    return 0 if (clean and clean.status == "completed") else 2


# --- Argparse setup --------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-pipeline image generation orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    # --- diversity-grid ---
    p_dg = sub.add_parser("diversity-grid", help="1 prompt x 3 pipelines side-by-side")
    p_dg.add_argument("--prompt", required=True)
    p_dg.add_argument("--seed", type=int, default=None)
    p_dg.add_argument("--width", type=int, default=1024)
    p_dg.add_argument("--height", type=int, default=1024)
    p_dg.add_argument("--pipeline", action="append", default=None,
                      choices=["flux2", "sd35", "zimage"],
                      help="Restrict to a subset (repeatable)")
    _add_common(p_dg)
    p_dg.set_defaults(func=_cmd_diversity_grid)

    # --- batch (v2 full path: ideate -> [clean] -> [polish]) ---
    p_b = sub.add_parser(
        "batch",
        help="ideate (3xN) -> [--clean] -> [--polish]; the whole pool is the deliverable")
    p_b.add_argument("--prompt", required=True)
    p_b.add_argument("--seed", type=int, default=None)
    p_b.add_argument("--width", type=int, default=1024)
    p_b.add_argument("--height", type=int, default=1024)
    p_b.add_argument("--num-candidates", type=int, default=1,
                     help="N seeds -> 3*N candidates total (default: 1)")
    p_b.add_argument("--ideation-mode", default="refined", choices=["fast", "refined"])
    p_b.add_argument("--clean", action="store_true",
                     help="Run clean-all (img2img) over every successful candidate")
    p_b.add_argument("--clean-backend", default="zimage-img2img",
                     choices=["zimage-img2img", "sd35-img2img", "flux2-img2img"],
                     help="img2img backend for clean-all (default: zimage-img2img, "
                          "preserves pre-P3 behaviour)")
    p_b.add_argument("--clean-model", default=None,
                     help="Model override for the clean backend "
                          "(default: backend default -- zimage-base / sd3.5-medium / "
                          "flux.2-klein-4b)")
    p_b.add_argument("--clean-prompt", default=None,
                     help="Bias prompt for clean-all (default: the batch --prompt)")
    p_b.add_argument("--clean-strength", type=float, default=CLEAN_DEFAULTS["strength"])
    p_b.add_argument("--clean-negative-prompt", default=None,
                     help="Honoured by zimage/sd35 backends; ignored by flux2")
    p_b.add_argument("--no-clean-cfg-normalization", action="store_true",
                     help="zimage backend only; ignored by sd35/flux2")
    p_b.add_argument("--polish", action="store_true",
                     help="Run polish-all (img2img) over every candidate "
                          "after ideate/clean")
    p_b.add_argument("--polish-backend", default=None,
                     choices=["zimage-img2img", "sd35-img2img", "flux2-img2img"],
                     help="Polish backend (default: auto-detect from each image's "
                          "sidecar; fallback sd35-img2img)")
    p_b.add_argument("--polish-model", default=None,
                     help="Model override for the polish backend "
                          "(default: backend default)")
    p_b.add_argument("--polish-prompt", default=None,
                     help="Polish prompt (default: sidecar prompt, "
                          "else the batch --prompt)")
    p_b.add_argument("--polish-negative-prompt", default="",
                     help="Honoured by sd35; ignored by zimage/flux2")
    p_b.add_argument("--polish-strength", type=float, default=0.22,
                     help="Polish img2img strength (0.20-0.25 typical; "
                          ">0.30 degrades)")
    p_b.add_argument("--polish-seed", type=int, default=None,
                     help="Polish seed (default: sidecar seed, "
                          "else the candidate seed)")
    p_b.add_argument("--img2img-batching", default="by-backend",
                     choices=["by-backend", "per-image"],
                     help="Execution order for clean/polish img2img "
                          "(by-backend groups same-backend runs consecutively; "
                          "records stay in candidate order either way)")
    _add_common(p_b)
    p_b.set_defaults(func=_cmd_batch)

    # --- compose-character (REMOVED in v2 -> migration stub) ---
    p_cc = sub.add_parser("compose-character",
                          help="[removed in v2 -- use `batch`]")
    p_cc.set_defaults(func=_cmd_removed, _removed_name="compose-character")

    # --- ideate (stage 1 only) ---
    p_id = sub.add_parser("ideate",
                          help="candidate-pool generation only (3xN), then stop")
    p_id.add_argument("--prompt", required=True)
    p_id.add_argument("--seed", type=int, default=None)
    p_id.add_argument("--num-candidates", type=int, default=1)
    p_id.add_argument("--ideation-mode", default="refined", choices=["fast", "refined"])
    p_id.add_argument("--width", type=int, default=1024)
    p_id.add_argument("--height", type=int, default=1024)
    _add_common(p_id)
    p_id.set_defaults(func=_cmd_ideate)

    # --- select (REMOVED in v2 -> migration stub) ---
    p_sel = sub.add_parser("select", help="[removed in v2 -- batch has no 'select']")
    p_sel.set_defaults(func=_cmd_removed, _removed_name="select")

    # --- clean (stage 3 only) ---
    p_cl = sub.add_parser("clean",
                          help="standalone Z-Image-Base img2img on one image")
    p_cl.add_argument("--input-image", required=True,
                      help="Image to clean (any source -- candidate, hand-painted, prior run output)")
    p_cl.add_argument("--prompt", required=True)
    p_cl.add_argument("--seed", type=int, default=None)
    p_cl.add_argument("--strength", type=float, default=CLEAN_DEFAULTS["strength"])
    p_cl.add_argument("--negative-prompt", default=None)
    p_cl.add_argument("--no-cfg-normalization", action="store_true")
    p_cl.add_argument("--width", type=int, default=1024)
    p_cl.add_argument("--height", type=int, default=1024)
    _add_common(p_cl)
    p_cl.set_defaults(func=_cmd_clean)

    # --- lineage ---
    p_li = sub.add_parser("lineage",
                          help="Walk session manifests and print a lineage tree")
    p_li.add_argument("target", nargs="?", default=None,
                      help="A session_id or a run_id. Omit when using --artifact.")
    p_li.add_argument("--artifact", default=None,
                      help="Look up a specific artifact_id across all sessions")
    p_li.add_argument("--sessions-dir", default="src/state/sessions")
    p_li.add_argument("--format", default="tree", choices=["tree", "json"])
    p_li.add_argument("--depth", type=int, default=None,
                      help="Max tree depth (default: unlimited)")
    p_li.add_argument("--show-failed", action="store_true",
                      help="Include failed runs (default: yes; use --no-show-failed to skip)")
    p_li.set_defaults(func=_cmd_lineage)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
