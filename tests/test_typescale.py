import re

import pytest

import typescale as ts


def _cfg(text):
    cfg, errors = ts.parse_config(text, name="t.scale")
    assert errors == []
    return cfg


def test_parses_full_config():
    cfg = _cfg(
        "base = 18px\nratio = Perfect-Fourth\nsteps = -1..3\n"
        "root = 16\nline = 1.6\nbaseline = 6\nfluid = 14 20 320 1200\n"
    )
    assert cfg.base == 18
    assert cfg.ratio == pytest.approx(1.333)
    assert cfg.ratio_name == "perfect fourth"
    assert cfg.steps == (-1, 3)
    assert cfg.root == 16
    assert cfg.line == 1.6
    assert cfg.baseline == 6
    assert cfg.fluid == (14, 20, 320, 1200)


def test_numeric_ratio_and_comments_and_blank_lines_are_fine():
    cfg = _cfg("# header comment\n\nratio = 1.5  # perfect fifth-ish\n")
    assert cfg.ratio == 1.5
    assert cfg.ratio_name == "1.5"


def test_errors_carry_file_and_line_numbers():
    text = "base = 16\nbse = 20\nratio = 0.9\nsteps 1..2\nbase = 3\n"
    _, errors = ts.parse_config(text, name="site.scale")
    assert "site.scale:2: unknown key 'bse'" in errors
    assert any(e.startswith("site.scale:3: ratio: ") for e in errors)
    assert "site.scale:4: expected 'key = value', got 'steps 1..2'" in errors
    assert "site.scale:5: duplicate key 'base' (first on line 1)" in errors


@pytest.mark.parametrize(
    "line, fragment",
    [
        ("base = 16pt", "not a number"),
        ("base = -4", "must be greater than 0"),
        ("ratio = fancy", "unknown ratio"),
        ("steps = 5..1", "low step is above high step"),
        ("steps = 0..40", "steps must stay within"),
        ("steps = -5..12", "at most 16 steps"),
        ("fluid = 20 14 320 1200", "base-min is above base-max"),
        ("fluid = 14 20 900 900", "viewport-min must be below viewport-max"),
        ("line = 4", "between 1 and 3"),
        ("baseline = 0", "must be greater than 0"),
    ],
)
def test_invalid_values_are_rejected(line, fragment):
    _, errors = ts.parse_config(line, name="x")
    assert len(errors) == 1
    assert fragment in errors[0]
    assert errors[0].startswith("x:1: ")


def test_scale_values_follow_the_ratio():
    cfg = _cfg("base = 16\nratio = major third\nsteps = -1..2\n")
    by_n = {s.n: s for s in ts.build_scale(cfg)}
    assert by_n[0].px == pytest.approx(16)
    assert by_n[1].px == pytest.approx(20)
    assert by_n[-1].px == pytest.approx(12.8)
    assert by_n[2].rem == pytest.approx(1.5625)


def test_line_height_snaps_to_baseline_grid_with_minimum():
    cfg = _cfg("base = 16\nratio = major third\nsteps = -2..1\nline = 1.5\nbaseline = 4\n")
    by_n = {s.n: s.line_px for s in ts.build_scale(cfg)}
    assert by_n[0] == 24          # 16 * 1.5 = 24, already on the grid
    assert by_n[1] == 32          # 30 rounds to 32 on a 4px grid
    assert by_n[-2] == 16         # 10.24 * 1.5 = 15.36 rounds to 16 on a 4px grid
    assert ts.snap_to_grid(3.0, 4) == 4  # never below one grid row


def test_fluid_clamp_hits_both_endpoints():
    cfg = _cfg("base = 16\nratio = major third\nsteps = 0..0\nroot = 16\nfluid = 14 18 320 1280\n")
    step = ts.build_scale(cfg)[0]
    css = ts.fluid_clamp(step, cfg)
    match = re.fullmatch(
        r"clamp\(([\d.]+)rem, ([-\d.]+)rem \+ ([-\d.]+)vw, ([\d.]+)rem\)", css
    )
    assert match, css
    low, intercept, slope_vw, high = (float(g) for g in match.groups())
    assert low == pytest.approx(14 / 16)
    assert high == pytest.approx(18 / 16)
    # The clamp's middle term is rem + vw, so convert the rem part to px before adding the vw part.
    value_px_at = lambda w: intercept * 16 + slope_vw * w / 100  # noqa: E731
    assert value_px_at(320) == pytest.approx(14, abs=0.01)
    assert value_px_at(1280) == pytest.approx(18, abs=0.01)


def test_css_output_has_steps_line_heights_and_fluid_variables():
    cfg = _cfg("base = 16\nsteps = -1..1\nline = 1.5\nfluid = 14 18 320 1280\n")
    css = ts.render_css(cfg, ts.build_scale(cfg))
    assert css.startswith(":root {")
    assert "  --step-0: 1rem;" in css
    assert "  --step--1: 0.8rem;" in css
    assert "  --line-step-0: 24px;" in css
    assert "  --fluid-step-1: clamp(" in css
    assert css.rstrip().endswith("}")


def test_lint_flags_off_scale_sizes_and_skips_variables():
    cfg = _cfg("base = 16\nratio = major third\nsteps = -2..5\n")
    steps = ts.build_scale(cfg)
    css = (
        "body { font-size: 1rem; }\n"
        "h1 { font-size: 2.4414rem; }\n"
        "h2 { font-size: 21px; }\n"
        "p  { font-size: 14px; }\n"
        "em { font-size: var(--step-1); }\n"
        ".x { font-size: clamp(1rem, 2vw, 2rem); }\n"
        "small { font-size: 0.8rem; }\n"
    )
    findings = ts.lint_css(css, steps, cfg.root, name="a.css")
    assert [(f.line, f.column) for f in findings] == [(3, 17), (4, 17)]
    assert findings[0].nearest.px == pytest.approx(20)
    assert findings[1].nearest.n == -1  # 14px is 1.2px from 12.8px, 2px from 16px


def test_lint_still_checks_minified_lines_with_variables():
    cfg = _cfg("base = 16\nsteps = 0..2\n")
    steps = ts.build_scale(cfg)
    css = "h2{font-size:21px}.a{font-size:var(--step-1)}\n"
    assert [f.line for f in ts.lint_css(css, steps, 16)] == [1]


def test_lint_tolerance_and_em_units():
    cfg = _cfg("base = 16\nsteps = 0..0\n")
    steps = ts.build_scale(cfg)
    css = "p { font-size: 16.4px; }\nq { font-size: 1em; }\n"
    assert ts.lint_css(css, steps, 16, tolerance=0.5) == []
    strict = ts.lint_css(css, steps, 16, tolerance=0.1)
    assert [f.line for f in strict] == [1]


def test_snap_finds_nearest_step_by_pixels():
    cfg = _cfg("base = 16\nratio = 2\nsteps = 0..3\n")
    steps = ts.build_scale(cfg)
    assert ts.nearest_step(steps, 17).n == 0
    assert ts.nearest_step(steps, 25).n == 1  # 25 is 9px from 16 but 7px from 32
    assert ts.nearest_step(steps, 100).n == 3


def test_json_export_structure():
    cfg = _cfg("base = 16\nratio = major third\nsteps = 0..1\nline = 1.5\n")
    data = ts.to_json_data(cfg, ts.build_scale(cfg))
    assert data["ratio_name"] == "major third"
    assert [s["step"] for s in data["steps"]] == [0, 1]
    assert data["steps"][1]["px"] == 20.0
    assert data["steps"][0]["line_height_px"] == 24


def test_table_lists_every_step_in_order():
    cfg = _cfg("base = 16\nsteps = -1..1\n")
    table = ts.render_table(cfg, ts.build_scale(cfg))
    body = table.splitlines()[3:]  # summary, blank line, header, then rows
    assert [row.split()[0] for row in body] == ["-1", "0", "1"]


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "ok.scale"
    good.write_text("base = 16\nsteps = 0..2\n", encoding="utf-8")
    bad = tmp_path / "bad.scale"
    bad.write_text("base = nope\n", encoding="utf-8")
    styles = tmp_path / "s.css"
    styles.write_text("h2 { font-size: 21px; }\n", encoding="utf-8")

    assert ts.main(["check", str(good)]) == 0
    assert "OK: 3 steps 0..2" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc:
        ts.main(["show", str(bad)])
    assert exc.value.code == 2

    assert ts.main(["lint", str(good), str(styles)]) == 1
    out = capsys.readouterr().out
    assert f"{styles}:1:" in out
    assert "1 off-scale font-size value(s)" in out


def test_snap_command_converts_rem(tmp_path, capsys):
    scale = tmp_path / "s.scale"
    scale.write_text("base = 16\nratio = 2\nsteps = 0..2\n", encoding="utf-8")
    assert ts.main(["snap", str(scale), "2", "--unit", "rem"]) == 0
    assert "step 1: 32px" in capsys.readouterr().out
