# typescale

**Plan a modular type scale from one plain-text file, then keep your CSS on it.**

`typescale` turns a base size, a ratio and a step range into px and rem values, line heights on a baseline grid, and fluid `clamp()` sizes. It writes CSS custom properties and JSON, snaps any size to its nearest step, and lints a stylesheet for `font-size` values that are off the scale. It runs offline with Python 3 standard library only.

## Features

- **Plain-text scale files** with `key = value` lines, `#` comments and inline comments.
- **Line-numbered errors** for unknown keys, duplicate keys, bad numbers, reversed step ranges and out-of-range values, reported as `file:line: message`.
- **Named ratios** such as `major third` or `perfect fifth`, or any custom number between 1 and 3. Run `typescale ratios` for the list.
- **Exact step table** with px and rem for every step, with rem derived from your root size.
- **Baseline-grid line heights**: each step's line height is snapped to a multiple of your baseline, never below one row.
- **Fluid type**: `clamp()` values that interpolate linearly between a minimum and maximum size across a viewport range.
- **CSS custom properties** (`--step-N`, `--line-step-N`, `--fluid-step-N`) with a configurable selector.
- **JSON export** for build pipelines.
- **Snap a size** to the nearest step, in px or rem, with the offset in px.
- **Lint a stylesheet** for `font-size` values that are off the scale, with `file:line:column` positions. `var()` and `clamp()` values are skipped, and `em` is treated as `rem`.
- **Tolerance control** for lint (`--tolerance`, default 0.5px).
- **CI-friendly exit codes**: `0` success, `1` off-scale sizes found, `2` config or read error.
- **Validate only** with `typescale check`, for use in a pre-commit hook.

## Quick start

The repo includes a sample scale and a stylesheet.

`examples/site.scale`:

```text
# Type scale for the marketing site
base = 16px          # body text size
ratio = major third  # 1.25
steps = -2..5
root = 16px
line = 1.5           # body line-height
baseline = 4px       # line heights snap to 4px rows
fluid = 15 18 320 1280
```

Check the scale, then lint the stylesheet:

```bash
python3 typescale.py check examples/site.scale
python3 typescale.py lint examples/site.scale examples/site.css
```

Expected output:

```text
OK: 8 steps -2..5, ratio major third, base 16px
examples/site.css:12:14: font-size: 21px is off the scale; nearest step 1 is 20px
1 off-scale font-size value(s)
```

The lint command exits with `1` here because `h2` uses `21px`, which is not on the scale. Change it to `20px` and the check passes.

Generate the CSS variables:

```bash
python3 typescale.py css examples/site.scale > tokens.css
```

The output starts like this:

```css
:root {
  --step--2: 0.64rem;
  --step--1: 0.8rem;
  --step-0: 1rem;
  --step-1: 1.25rem;
  ...
  --line-step-0: 24px;
  ...
}
```

## How to run

Requires Python 3.10 or newer. No install step and no network access.

| Command | What it does |
| --- | --- |
| `python3 typescale.py show FILE` | Print the scale as a table |
| `python3 typescale.py css FILE [--selector SEL]` | Emit CSS custom properties |
| `python3 typescale.py json FILE` | Emit the scale as JSON |
| `python3 typescale.py snap FILE VALUE [--unit px\|rem]` | Find the nearest step to a size |
| `python3 typescale.py lint FILE CSS [--tolerance PX]` | Flag off-scale `font-size` values |
| `python3 typescale.py check FILE` | Validate the scale file only |
| `python3 typescale.py ratios` | List named ratios |

Examples:

```bash
python3 typescale.py show examples/site.scale
python3 typescale.py snap examples/site.scale 2.5 --unit rem
python3 typescale.py json examples/site.scale
```

## Scale file reference

| Key | Default | Meaning |
| --- | --- | --- |
| `base` | `16` | Step 0 size in px |
| `ratio` | `major third` | Named ratio or number in (1, 3] |
| `steps` | `-2..5` | Inclusive step range within -10..20, at most 16 steps |
| `root` | `16` | px per rem |
| `line` | none | Unitless line-height; omit to skip line heights |
| `baseline` | `4` | Grid in px for line heights, between 1 and 64 |
| `fluid` | none | `base-min base-max viewport-min viewport-max` in px |

## How to test

```bash
python3 -m pytest -q
```

The tests cover parsing and its error messages, scale maths, baseline snapping, fluid clamp endpoints, CSS and JSON output, lint positions and tolerance, snapping, and CLI exit codes. They need `pytest` only.

## Notes

- Fluid sizes are interpolated between your `fluid` base sizes, scaled by the same ratio as the static scale.
- `em` values in lint are treated as `rem`, which assumes the parent is at the root size.
- Lint only looks at `font-size` declarations. It does not parse full CSS, so comments containing `font-size:` on the same line may be flagged.
