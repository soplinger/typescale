#!/usr/bin/env python3
"""typescale: modular type-scale planner driven by a plain-text scale file."""

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

NAMED_RATIOS = {
    "minor second": 1.067,
    "major second": 1.125,
    "minor third": 1.2,
    "major third": 1.25,
    "perfect fourth": 1.333,
    "augmented fourth": 1.414,
    "perfect fifth": 1.5,
    "golden ratio": 1.618,
    "major sixth": 1.667,
    "minor seventh": 1.778,
    "major seventh": 1.875,
    "octave": 2.0,
}

KEYS = ("base", "ratio", "steps", "baseline", "line", "root", "fluid")
MAX_STEPS = 16
STEP_LIMITS = (-10, 20)
BASELINE_LIMITS = (1, 64)
DECL_RE = re.compile(r"font-size\s*:\s*([0-9]*\.?[0-9]+)\s*(px|rem|em)\b", re.IGNORECASE)


@dataclass
class Config:
    base: float = 16.0
    ratio: float = 1.25
    ratio_name: str = "major third"
    steps: tuple = (-2, 5)
    baseline: float = 4.0
    line: float | None = None
    root: float = 16.0
    fluid: tuple | None = None  # (base-min px, base-max px, viewport-min px, viewport-max px)


@dataclass(frozen=True)
class Step:
    n: int
    px: float
    rem: float
    line_px: float | None = None
    fluid_min_px: float | None = None
    fluid_max_px: float | None = None


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    column: int
    text: str
    px: float
    nearest: Step


# --- parsing -----------------------------------------------------------------

def _number(text):
    cleaned = re.sub(r"px$", "", text.strip(), flags=re.IGNORECASE).strip()
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError(f"not a number: {text!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {text!r}")
    return value


def _positive(text, upper=None):
    value = _number(text)
    if value <= 0:
        raise ValueError("must be greater than 0")
    if upper is not None and value > upper:
        raise ValueError(f"must be at most {upper:g}")
    return value


def _ratio(text):
    key = " ".join(text.lower().replace("-", " ").split())
    if key in NAMED_RATIOS:
        return NAMED_RATIOS[key], key
    try:
        number = _number(text)
    except ValueError:
        raise ValueError(
            f"unknown ratio {text!r}; use a number or a name from 'typescale ratios'"
        ) from None
    if not 1 < number <= 3:
        raise ValueError("ratio must be between 1 and 3")
    return number, f"{number:g}"


def _steps(text):
    match = re.fullmatch(r"(-?\d+)\s*\.\.\s*(-?\d+)", text.strip())
    if not match:
        raise ValueError(f"expected 'low..high' integers, got {text!r}")
    low, high = int(match[1]), int(match[2])
    if low > high:
        raise ValueError("low step is above high step")
    if low < STEP_LIMITS[0] or high > STEP_LIMITS[1]:
        raise ValueError(f"steps must stay within {STEP_LIMITS[0]}..{STEP_LIMITS[1]}")
    if high - low + 1 > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps are allowed")
    return low, high


def _fluid(text):
    parts = text.split()
    if len(parts) != 4:
        raise ValueError("expected 'base-min base-max viewport-min viewport-max'")
    base_min, base_max, vmin, vmax = (_positive(part) for part in parts)
    if base_min > base_max:
        raise ValueError("base-min is above base-max")
    if vmin >= vmax:
        raise ValueError("viewport-min must be below viewport-max")
    return base_min, base_max, vmin, vmax


def _apply(cfg, key, value):
    if key == "base":
        cfg.base = _positive(value)
    elif key == "root":
        cfg.root = _positive(value)
    elif key == "ratio":
        cfg.ratio, cfg.ratio_name = _ratio(value)
    elif key == "steps":
        cfg.steps = _steps(value)
    elif key == "baseline":
        cfg.baseline = _positive(value, upper=BASELINE_LIMITS[1])
    elif key == "line":
        number = _number(value)
        if not 1 <= number <= 3:
            raise ValueError("line-height must be between 1 and 3")
        cfg.line = number
    elif key == "fluid":
        cfg.fluid = _fluid(value)


def parse_config(text, name="scale"):
    """Return (Config, errors). Errors are 'name:line: message' strings."""
    cfg = Config()
    errors = []
    seen = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            errors.append(f"{name}:{lineno}: expected 'key = value', got {line!r}")
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        key = key.lower()
        if key not in KEYS:
            errors.append(f"{name}:{lineno}: unknown key {key!r}")
            continue
        if key in seen:
            errors.append(f"{name}:{lineno}: duplicate key {key!r} (first on line {seen[key]})")
            continue
        seen[key] = lineno
        if not value:
            errors.append(f"{name}:{lineno}: {key} has no value")
            continue
        try:
            _apply(cfg, key, value)
        except ValueError as exc:
            errors.append(f"{name}:{lineno}: {key}: {exc}")
    return cfg, errors


# --- scale maths -------------------------------------------------------------

def snap_to_grid(px, grid):
    return max(grid, math.floor(px / grid + 0.5) * grid)


def build_scale(cfg):
    low, high = cfg.steps
    steps = []
    for n in range(low, high + 1):
        px = cfg.base * cfg.ratio ** n
        line_px = None
        if cfg.line is not None:
            line_px = snap_to_grid(px * cfg.line, cfg.baseline)
        fluid_min = fluid_max = None
        if cfg.fluid:
            fluid_min = cfg.fluid[0] * cfg.ratio ** n
            fluid_max = cfg.fluid[1] * cfg.ratio ** n
        steps.append(Step(n, px, px / cfg.root, line_px, fluid_min, fluid_max))
    return steps


def _trim(value, places=4):
    text = f"{value:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def fluid_clamp(step, cfg):
    vmin, vmax = cfg.fluid[2], cfg.fluid[3]
    slope = (step.fluid_max_px - step.fluid_min_px) / (vmax - vmin)
    intercept = step.fluid_min_px - slope * vmin
    return (
        f"clamp({_trim(step.fluid_min_px / cfg.root)}rem, "
        f"{_trim(intercept / cfg.root)}rem + {_trim(slope * 100)}vw, "
        f"{_trim(step.fluid_max_px / cfg.root)}rem)"
    )


def nearest_step(steps, px):
    return min(steps, key=lambda s: (abs(s.px - px), s.n))


def lint_css(text, steps, root, tolerance=0.5, name="styles.css"):
    findings = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        # DECL_RE needs a number right after the colon, so var(), clamp() and calc() never match.
        for match in DECL_RE.finditer(raw):
            value = float(match[1])
            unit = match[2].lower()
            px = value * root if unit in ("rem", "em") else value
            nearest = nearest_step(steps, px)
            if abs(nearest.px - px) > tolerance:
                findings.append(
                    Finding(name, lineno, match.start(1) + 1, match[0], px, nearest)
                )
    return findings


# --- rendering ---------------------------------------------------------------

def render_table(cfg, steps):
    has_line = cfg.line is not None
    header = ["step", "px", "rem"] + (["line-height px"] if has_line else [])
    rows = []
    for s in steps:
        row = [str(s.n), _trim(s.px, 2), _trim(s.rem)]
        if has_line:
            row.append(_trim(s.line_px, 2))
        rows.append(row)
    widths = [max(len(r[i]) for r in [header] + rows) for i in range(len(header))]
    summary = (
        f"base {_trim(cfg.base, 2)}px  ratio {cfg.ratio:g} ({cfg.ratio_name})  "
        f"root {_trim(cfg.root, 2)}px"
    )
    lines = [summary, ""]
    for row in [header] + rows:
        lines.append("  ".join(cell.rjust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def render_css(cfg, steps, selector=":root"):
    lines = [f"{selector} {{"]
    for s in steps:
        lines.append(f"  --step-{s.n}: {_trim(s.rem)}rem;")
    if cfg.line is not None:
        for s in steps:
            lines.append(f"  --line-step-{s.n}: {_trim(s.line_px, 2)}px;")
    if cfg.fluid:
        for s in steps:
            lines.append(f"  --fluid-step-{s.n}: {fluid_clamp(s, cfg)};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def to_json_data(cfg, steps):
    data = {
        "base_px": cfg.base,
        "ratio": cfg.ratio,
        "ratio_name": cfg.ratio_name,
        "root_px": cfg.root,
        "steps": [],
    }
    for s in steps:
        entry = {"step": s.n, "px": round(s.px, 2), "rem": round(s.rem, 4)}
        if s.line_px is not None:
            entry["line_height_px"] = s.line_px
        if cfg.fluid:
            entry["fluid_clamp"] = fluid_clamp(s, cfg)
        data["steps"].append(entry)
    return data


# --- CLI ---------------------------------------------------------------------

def _load(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"typescale: cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    cfg, errors = parse_config(text, name=path)
    if errors:
        for message in errors:
            print(message, file=sys.stderr)
        raise SystemExit(2)
    return cfg, build_scale(cfg)


def cmd_show(args):
    cfg, steps = _load(args.file)
    print(render_table(cfg, steps))
    return 0


def cmd_css(args):
    cfg, steps = _load(args.file)
    print(render_css(cfg, steps, selector=args.selector), end="")
    return 0


def cmd_json(args):
    cfg, steps = _load(args.file)
    print(json.dumps(to_json_data(cfg, steps), indent=2))
    return 0


def cmd_snap(args):
    cfg, steps = _load(args.file)
    px = args.value * cfg.root if args.unit == "rem" else args.value
    nearest = nearest_step(steps, px)
    delta = px - nearest.px
    print(
        f"{_trim(args.value, 4)}{args.unit} -> step {nearest.n}: "
        f"{_trim(nearest.px, 2)}px ({_trim(nearest.rem)}rem), "
        f"off by {delta:+.2f}px"
    )
    return 0


def cmd_lint(args):
    cfg, steps = _load(args.file)
    try:
        css = Path(args.css).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"typescale: cannot read {args.css}: {exc}", file=sys.stderr)
        return 2
    findings = lint_css(css, steps, cfg.root, tolerance=args.tolerance, name=args.css)
    for f in findings:
        print(
            f"{f.file}:{f.line}:{f.column}: {f.text.strip()} is off the scale; "
            f"nearest step {f.nearest.n} is {_trim(f.nearest.px, 2)}px"
        )
    if findings:
        print(f"{len(findings)} off-scale font-size value(s)")
        return 1
    print("OK: every font-size is on the scale")
    return 0


def cmd_check(args):
    cfg, steps = _load(args.file)
    print(
        f"OK: {len(steps)} steps {steps[0].n}..{steps[-1].n}, "
        f"ratio {cfg.ratio_name}, base {_trim(cfg.base, 2)}px"
    )
    return 0


def cmd_ratios(_args):
    for name, value in NAMED_RATIOS.items():
        print(f"{name:<18} {value}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="typescale",
        description="Offline modular type-scale planner driven by a plain-text scale file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("show", help="print the scale as a table")
    p.add_argument("file")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("css", help="emit CSS custom properties")
    p.add_argument("file")
    p.add_argument("--selector", default=":root", help="selector to wrap the variables")
    p.set_defaults(func=cmd_css)

    p = sub.add_parser("json", help="emit the scale as JSON")
    p.add_argument("file")
    p.set_defaults(func=cmd_json)

    p = sub.add_parser("snap", help="find the nearest step to a size")
    p.add_argument("file")
    p.add_argument("value", type=float)
    p.add_argument("--unit", choices=("px", "rem"), default="px")
    p.set_defaults(func=cmd_snap)

    p = sub.add_parser("lint", help="flag font-size values in a CSS file that are off the scale")
    p.add_argument("file")
    p.add_argument("css")
    p.add_argument("--tolerance", type=float, default=0.5, help="allowed px difference (default 0.5)")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("check", help="validate a scale file without printing the scale")
    p.add_argument("file")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("ratios", help="list named ratios")
    p.set_defaults(func=cmd_ratios)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
