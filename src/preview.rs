//! Render the exported winding instructions independently of the search raster.
use crate::{InvalidInput, Settings, raster::Canvas};
use image::{GrayImage, imageops};

pub fn render_preview(
    settings: &Settings,
    path: &[usize],
    size: u32,
) -> Result<GrayImage, InvalidInput> {
    settings.validate()?;
    if path.len() > 100_001
        || !(64..=2048).contains(&size)
        || path.is_empty()
        || path.iter().any(|&a| a >= settings.anchors)
        || path.windows(2).any(|p| p[0] == p[1])
    {
        return Err(InvalidInput("invalid preview size or anchor sequence"));
    }
    let fine = (size as usize * 2).max(2048);
    let canvas = Canvas::for_settings(fine, settings);
    let width = settings.thread_mm / settings.diameter_mm * (fine - 1) as f64;
    let mut darkness = vec![0.0; fine * fine];
    for pair in path.windows(2) {
        canvas.rasterize(canvas.points[pair[0]], canvas.points[pair[1]], width, |s| {
            let value = &mut darkness[s.index as usize];
            *value += (1.0 - *value) * s.coverage * settings.opacity;
        });
    }
    Ok(imageops::resize(
        &canvas.render(&darkness),
        size,
        size,
        imageops::FilterType::Lanczos3,
    ))
}
