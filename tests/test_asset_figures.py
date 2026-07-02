from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from idea_scout.asset_figures import crop_main_figure_from_page, best_figure_page, page_visual_score, store_relative_path


def test_best_figure_page_prefers_method_over_result_table() -> None:
    pages = {
        1: "Abstract. We introduce inverse autoregressive flow for variational inference.",
        2: "Figure 1: Prior distribution, posteriors in standard VAE, and posteriors in VAE with IAF.",
        5: "Table 1: MNIST likelihood results and standard deviations.",
        6: "Figure 3: Samples from CIFAR-10 after training.",
    }

    selected = best_figure_page(
        pages,
        query_terms=["posterior", "vae", "iaf", "flow", "jacobian"],
    )

    assert selected == 2


def test_store_relative_path_rejects_paths_outside_store(tmp_path: Path) -> None:
    store = tmp_path / "store"
    inside = store / "figures" / "a.png"
    outside = tmp_path / "elsewhere" / "a.png"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"png")
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"png")

    assert store_relative_path(inside, store) == "figures/a.png"
    assert store_relative_path(outside, store) == ""


def test_page_visual_score_prefers_nonblank_visual_content(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    visual = tmp_path / "visual.png"
    Image.new("RGB", (240, 240), "white").save(blank)
    img = Image.new("RGB", (240, 240), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((28, 28, 210, 210), outline="black", width=8)
    draw.line((40, 160, 200, 70), fill="black", width=6)
    draw.ellipse((80, 80, 130, 130), fill="black")
    img.save(visual)

    assert page_visual_score(visual) > page_visual_score(blank)


def test_crop_main_figure_from_page_uses_caption_region(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    cropped = tmp_path / "figure-main-page-1.png"
    img = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((150, 160, 760, 480), outline="black", width=8)
    draw.line((190, 430, 720, 205), fill="black", width=10)
    draw.text((150, 510), "Figure 1: Main architecture overview.", fill="black")
    draw.text((90, 850), "Body text below the figure should not dominate the crop.", fill="black")
    img.save(page)

    result = crop_main_figure_from_page(page, cropped)

    assert result == cropped
    with Image.open(cropped) as out:
        width, height = out.size
    assert width < 760
    assert height < 520
    assert width > 500
    assert height > 260
