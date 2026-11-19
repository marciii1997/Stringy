use std::f64::consts::{FRAC_PI_2, TAU};

use image::{DynamicImage, GrayImage, Luma, Pixel};
use serde::Serialize;

#[derive(Clone, Copy)]
pub(crate) struct Point {
    pub x: f64,
    pub y: f64,
}

#[derive(Debug, Serialize)]
pub struct Anchor {
    pub id: usize,
    pub x_mm: f64,
    pub y_mm: f64,
}

pub(crate) struct Canvas {
    pub anchors: Vec<Anchor>,
    pub points: Vec<Point>,
    pub inside: Vec<bool>,
    pub size: usize,
}

impl Canvas {
    pub fn new(size: usize, diameter_mm: f64, anchor_count: usize) -> Self {
        let radius_px = (size - 1) as f64 / 2.0;
        let radius_mm = diameter_mm / 2.0;
        let mut anchors = Vec::with_capacity(anchor_count);
        let mut points = Vec::with_capacity(anchor_count);
        for id in 0..anchor_count {
            let angle = TAU * id as f64 / anchor_count as f64 - FRAC_PI_2;
            let (y, x) = angle.sin_cos();
            anchors.push(Anchor {
                id,
                x_mm: x * radius_mm,
                y_mm: y * radius_mm,
            });
            points.push(Point {
                x: radius_px * (1.0 + x),
                y: radius_px * (1.0 + y),
            });
        }
        let inside = (0..size * size)
            .map(|pixel| {
                let x = (pixel % size) as f64 - radius_px;
                let y = (pixel / size) as f64 - radius_px;
                x * x + y * y <= radius_px * radius_px
            })
            .collect();
        Self {
            anchors,
            points,
            inside,
            size,
        }
    }

    pub fn target(&self, source: &DynamicImage, settings: &crate::Settings) -> Vec<f32> {
        let mut gray = GrayImage::new(source.width(), source.height());
        for (dest, rgba) in gray.pixels_mut().zip(source.to_rgba8().pixels()) {
            let luma = u32::from(rgba.to_luma()[0]);
            let alpha = u32::from(rgba[3]);
            *dest = Luma([((luma * alpha + 255 * (255 - alpha) + 127) / 255) as u8]);
        }
        let crop_size = (f64::from(gray.width().min(gray.height())) / settings.zoom)
            .round()
            .max(1.0) as u32;
        let offset = |length: u32, center: f64| {
            ((f64::from(length) * center / 100.0 - f64::from(crop_size) / 2.0).floor() as u32)
                .min(length - crop_size)
        };
        let crop = image::imageops::crop_imm(
            &gray,
            offset(gray.width(), settings.center_x),
            offset(gray.height(), settings.center_y),
            crop_size,
            crop_size,
        );
        image::imageops::resize(
            &*crop,
            self.size as u32,
            self.size as u32,
            image::imageops::FilterType::Lanczos3,
        )
        .pixels()
        .zip(&self.inside)
        .map(|(p, &inside)| {
            if inside {
                1.0 - (f32::from(p[0]) / 255.0).powf(settings.gamma)
            } else {
                0.0
            }
        })
        .collect()
    }

    pub fn render(&self, darkness: &[f32]) -> GrayImage {
        let pixels = darkness
            .iter()
            .map(|&value| ((1.0 - value).clamp(0.0, 1.0) * 255.0).round() as u8)
            .collect();
        GrayImage::from_raw(self.size as u32, self.size as u32, pixels)
            .expect("darkness buffer matches canvas size")
    }

    // Integrate the strip across each major-axis pixel using two midpoint samples.
    // Each minor-axis pixel appears once, so its coverage can be composited once.
    pub fn rasterize(
        &self,
        mut start: Point,
        mut end: Point,
        width_px: f64,
        mut visit: impl FnMut(Sample),
    ) {
        let swap_axes = (end.y - start.y).abs() > (end.x - start.x).abs();
        if swap_axes {
            std::mem::swap(&mut start.x, &mut start.y);
            std::mem::swap(&mut end.x, &mut end.y);
        }
        if start.x > end.x {
            std::mem::swap(&mut start, &mut end);
        }
        let slope = (end.y - start.y) / (end.x - start.x);
        let half_width = width_px * (1.0 + slope * slope).sqrt() / 2.0;
        let first = (start.x + 0.5).floor() as usize;
        let last = ((end.x + 0.5).floor() as usize).min(self.size - 1);
        for major in first..=last {
            let left = (major as f64 - 0.5).max(start.x);
            let right = (major as f64 + 0.5).min(end.x);
            let half_step = (right - left) / 2.0;
            let y0 = start.y + (left + 0.5 * half_step - start.x) * slope;
            let y1 = y0 + half_step * slope;
            let low = ((y0.min(y1) - half_width + 0.5).floor() as usize).min(self.size);
            let high = ((y0.max(y1) + half_width + 0.5).floor() as usize).min(self.size - 1);
            for minor in low..=high {
                let index = if swap_axes {
                    major * self.size + minor
                } else {
                    minor * self.size + major
                };
                if !self.inside[index] {
                    continue;
                }
                let lower = minor as f64 - 0.5;
                let upper = lower + 1.0;
                let overlap =
                    |y: f64| ((y + half_width).min(upper) - (y - half_width).max(lower)).max(0.0);
                let coverage = half_step * (overlap(y0) + overlap(y1));
                if coverage > 0.0 {
                    visit(Sample {
                        index: index as u32,
                        coverage: coverage as f32,
                    });
                }
            }
        }
    }
}

#[derive(Clone, Copy)]
pub(crate) struct Sample {
    pub index: u32,
    pub coverage: f32,
}
