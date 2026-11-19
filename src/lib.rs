mod raster;

use std::{
    fmt,
    mem::size_of,
    ops::{ControlFlow, Range},
};

use image::{DynamicImage, GrayImage};
use raster::{Canvas, Sample};
use rayon::prelude::*;
use serde::Serialize;

pub use raster::Anchor;

const MIB: usize = 1024 * 1024;
const MIN_GAIN: f64 = 1e-8;

#[derive(Debug, Clone, Serialize)]
pub struct Settings {
    pub anchors: usize,
    pub diameter_mm: f64,
    pub thread_mm: f64,
    pub opacity: f32,
    pub max_segments: usize,
    pub raster_size: usize,
    pub min_anchor_gap: usize,
    pub start_anchor: usize,
    pub repeat_penalty: f64,
    pub cache_mib: usize,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            anchors: 360,
            diameter_mm: 600.0,
            thread_mm: 0.25,
            opacity: 1.0,
            max_segments: 12_000,
            raster_size: 512,
            min_anchor_gap: 8,
            start_anchor: 0,
            repeat_penalty: 0.0005,
            cache_mib: 384,
        }
    }
}

#[derive(Debug)]
pub struct InvalidInput(&'static str);

impl fmt::Display for InvalidInput {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

impl std::error::Error for InvalidInput {}

impl Settings {
    fn validate(&self, source: &DynamicImage) -> Result<(), InvalidInput> {
        if source.width() == 0 || source.height() == 0 {
            return Err(InvalidInput("source image must have nonzero dimensions"));
        }
        if !(3..=720).contains(&self.anchors) {
            return Err(InvalidInput("anchors must be between 3 and 720"));
        }
        if !(64..=1024).contains(&self.raster_size) {
            return Err(InvalidInput(
                "raster size must be between 64 and 1024 pixels",
            ));
        }
        if !self.diameter_mm.is_finite() || !(1.0..=100_000.0).contains(&self.diameter_mm) {
            return Err(InvalidInput(
                "canvas diameter must be between 1 and 100000 mm",
            ));
        }
        if !self.thread_mm.is_finite()
            || !(0.001..=self.diameter_mm / 10.0).contains(&self.thread_mm)
        {
            return Err(InvalidInput(
                "thread diameter must be between 0.001 mm and one tenth of the canvas diameter",
            ));
        }
        if !(0.0 < self.opacity && self.opacity <= 1.0) {
            return Err(InvalidInput(
                "opacity must be greater than zero and at most one",
            ));
        }
        if !(1..=self.anchors / 2).contains(&self.min_anchor_gap) {
            return Err(InvalidInput(
                "minimum anchor gap must be between 1 and half the anchor count",
            ));
        }
        if self.start_anchor >= self.anchors {
            return Err(InvalidInput("start anchor is outside the anchor range"));
        }
        if self.max_segments > 100_000 {
            return Err(InvalidInput("maximum segment count must be at most 100000"));
        }
        if !self.repeat_penalty.is_finite() || self.repeat_penalty < 0.0 {
            return Err(InvalidInput(
                "repeat penalty must be finite and nonnegative",
            ));
        }
        if self.cache_mib > 1024 {
            return Err(InvalidInput("line cache budget must be at most 1024 MiB"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StopReason {
    SegmentLimit,
    NoImprovingMove,
    Cancelled,
}

#[derive(Debug)]
pub struct Progress {
    pub segments: usize,
    pub anchor: usize,
}

#[derive(Debug, Serialize)]
pub struct Segment {
    pub from: usize,
    pub to: usize,
    pub length_mm: f64,
}

#[derive(Debug, Serialize)]
pub struct Route {
    pub format_version: u32,
    pub settings: Settings,
    pub anchors: Vec<Anchor>,
    pub path: Vec<usize>,
    pub segments: Vec<Segment>,
    pub total_length_m: f64,
    pub initial_rmse: f64,
    pub final_rmse: f64,
    pub stop_reason: StopReason,
}

pub struct Artwork {
    pub route: Route,
    pub target: GrayImage,
    pub preview: GrayImage,
    pub cache_bytes_used: usize,
}

#[derive(Clone, Copy)]
struct Move {
    anchor: usize,
    line: usize,
}

struct Line {
    endpoints: (usize, usize),
    length_mm: f64,
    samples: Range<usize>,
}

struct Lines {
    lines: Vec<Line>,
    moves: Vec<Vec<Move>>,
    cache: Vec<Sample>,
    width_px: f64,
}

impl Lines {
    fn new(canvas: &Canvas, settings: &Settings) -> Self {
        let width_px = settings.thread_mm / settings.diameter_mm * (canvas.size - 1) as f64;
        let sample_capacity = settings.cache_mib * MIB / size_of::<Sample>();
        let mut lines = Vec::new();
        let mut moves = vec![Vec::new(); settings.anchors];
        let mut cache = Vec::new();
        let mut scratch = Vec::new();
        let mut cache_has_room = sample_capacity > 0 && settings.max_segments > 0;

        for from in 0..settings.anchors {
            for to in from + settings.min_anchor_gap..settings.anchors {
                if settings.anchors - (to - from) < settings.min_anchor_gap {
                    continue;
                }
                let id = lines.len();
                moves[from].push(Move {
                    anchor: to,
                    line: id,
                });
                moves[to].push(Move {
                    anchor: from,
                    line: id,
                });
                let length_mm = (canvas.anchors[to].x_mm - canvas.anchors[from].x_mm)
                    .hypot(canvas.anchors[to].y_mm - canvas.anchors[from].y_mm);
                let start = cache.len();
                if cache_has_room {
                    scratch.clear();
                    canvas.rasterize(canvas.points[from], canvas.points[to], width_px, |s| {
                        scratch.push(s)
                    });
                    if scratch.len() <= sample_capacity - start {
                        let needed = start + scratch.len();
                        if needed > cache.capacity() {
                            let next = needed.max(cache.capacity() * 2).min(sample_capacity);
                            cache.reserve_exact(next - start);
                        }
                        cache.extend_from_slice(&scratch);
                    } else {
                        cache_has_room = false;
                    }
                }
                lines.push(Line {
                    endpoints: (from, to),
                    length_mm,
                    samples: start..cache.len(),
                });
            }
        }
        Self {
            lines,
            moves,
            cache,
            width_px,
        }
    }

    fn visit(&self, canvas: &Canvas, id: usize, mut callback: impl FnMut(Sample)) {
        let line = &self.lines[id];
        if line.samples.is_empty() {
            let (from, to) = line.endpoints;
            canvas.rasterize(
                canvas.points[from],
                canvas.points[to],
                self.width_px,
                callback,
            );
        } else {
            for &sample in &self.cache[line.samples.clone()] {
                callback(sample);
            }
        }
    }
}

#[derive(Clone, Copy)]
struct PixelTerms {
    linear: f32,
    quadratic: f32,
}

impl PixelTerms {
    fn new(target: f32, current: f32) -> Self {
        let remaining = 1.0 - current;
        Self {
            linear: 2.0 * (target - current) * remaining,
            quadratic: remaining * remaining,
        }
    }

    // C' = C + alpha * (1 - C). Expand (T - C)^2 - (T - C')^2.
    fn gain(self, alpha: f32) -> f32 {
        alpha * (self.linear - alpha * self.quadratic)
    }
}

fn rmse(canvas: &Canvas, target: &[f32], current: &[f32]) -> f64 {
    let mut squared_error = 0.0;
    let mut pixels = 0;
    for ((&target, &current), &inside) in target.iter().zip(current).zip(&canvas.inside) {
        if inside {
            squared_error += f64::from(target - current).powi(2);
            pixels += 1;
        }
    }
    (squared_error / f64::from(pixels)).sqrt()
}

/// Generate one continuous route. Progress is reported before each segment search.
/// Returning Break yields the artwork and partial route computed so far.
pub fn generate(
    source: &DynamicImage,
    settings: &Settings,
    mut progress: impl FnMut(Progress) -> ControlFlow<()>,
) -> Result<Artwork, InvalidInput> {
    settings.validate(source)?;
    let canvas = Canvas::new(settings.raster_size, settings.diameter_mm, settings.anchors);
    let target = canvas.target(source);
    let mut current = vec![0.0; target.len()];
    let mut terms: Vec<_> = target.iter().map(|&t| PixelTerms::new(t, 0.0)).collect();
    let initial_rmse = rmse(&canvas, &target, &current);
    let lines = Lines::new(&canvas, settings);
    let mut repeats = vec![0u32; lines.lines.len()];
    let mut path = vec![settings.start_anchor];
    let mut segments = Vec::with_capacity(settings.max_segments);
    let mut total_length_mm = 0.0;
    let mut stop_reason = StopReason::SegmentLimit;

    while segments.len() < settings.max_segments {
        let from = *path.last().expect("path includes its starting anchor");
        if progress(Progress {
            segments: segments.len(),
            anchor: from,
        })
        .is_break()
        {
            stop_reason = StopReason::Cancelled;
            break;
        }
        let best = lines.moves[from]
            .par_iter()
            .with_min_len(24)
            .filter_map(|&candidate| {
                let mut gain = 0.0;
                lines.visit(&canvas, candidate.line, |sample| {
                    gain += f64::from(
                        terms[sample.index as usize].gain(settings.opacity * sample.coverage),
                    );
                });
                let score = gain - settings.repeat_penalty * f64::from(repeats[candidate.line]);
                (score > MIN_GAIN).then_some((score, candidate))
            })
            .max_by(|(score_a, a), (score_b, b)| {
                score_a
                    .total_cmp(score_b)
                    .then_with(|| b.anchor.cmp(&a.anchor))
            });

        let Some((_, winner)) = best else {
            stop_reason = StopReason::NoImprovingMove;
            break;
        };
        lines.visit(&canvas, winner.line, |sample| {
            let index = sample.index as usize;
            let darkness = &mut current[index];
            let alpha = settings.opacity * sample.coverage;
            *darkness += (1.0 - *darkness) * alpha;
            terms[index] = PixelTerms::new(target[index], *darkness);
        });
        repeats[winner.line] += 1;
        let length_mm = lines.lines[winner.line].length_mm;
        total_length_mm += length_mm;
        path.push(winner.anchor);
        segments.push(Segment {
            from,
            to: winner.anchor,
            length_mm,
        });
    }

    Ok(Artwork {
        target: canvas.render(&target),
        preview: canvas.render(&current),
        cache_bytes_used: lines.cache.len() * size_of::<Sample>(),
        route: Route {
            format_version: 1,
            final_rmse: rmse(&canvas, &target, &current),
            initial_rmse,
            settings: settings.clone(),
            anchors: canvas.anchors,
            path,
            segments,
            total_length_m: total_length_mm / 1000.0,
            stop_reason,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{Luma, Rgba, RgbaImage};

    #[test]
    fn score_matches_error_reduction_with_overlapping_ink() {
        let (target, previous, alpha) = (0.7_f32, 0.35_f32, 0.2_f32);
        let next = previous + alpha * (1.0 - previous);
        let expected = (target - previous).powi(2) - (target - next).powi(2);
        assert!((PixelTerms::new(target, previous).gain(alpha) - expected).abs() < 1e-6);
    }

    #[test]
    fn preprocessing_crops_the_center_and_composites_transparency_on_white() {
        let mut source = RgbaImage::from_pixel(128, 64, Rgba([0, 0, 0, 255]));
        for x in 32..96 {
            for y in 0..64 {
                source.put_pixel(x, y, Rgba([0, 0, 0, 0]));
            }
        }
        let canvas = Canvas::new(64, 600.0, 32);
        assert!((canvas.anchors[0].y_mm + 300.0).abs() < 1e-9);
        assert!((canvas.anchors[8].x_mm - 300.0).abs() < 1e-9);
        let target = canvas.target(&DynamicImage::ImageRgba8(source));
        assert!(target.into_iter().all(|darkness| darkness == 0.0));
    }

    #[test]
    fn route_is_continuous_and_unchanged_when_rasterizing_on_demand() {
        let source = DynamicImage::ImageLuma8(GrayImage::from_fn(72, 72, |x, y| {
            let stripe = x.abs_diff(35) < 7 || y.abs_diff(35) < 3;
            Luma([if stripe { 32 } else { 220 }])
        }));
        let mut settings = Settings {
            anchors: 40,
            raster_size: 72,
            max_segments: 60,
            min_anchor_gap: 2,
            ..Settings::default()
        };
        let cached = generate(&source, &settings, |_| ControlFlow::Continue(())).unwrap();
        settings.cache_mib = 0;
        let uncached = generate(&source, &settings, |_| ControlFlow::Continue(())).unwrap();
        assert!(cached.cache_bytes_used > 0);
        assert_eq!(uncached.cache_bytes_used, 0);
        let route = cached.route;
        assert_eq!(route.path, uncached.route.path);
        assert_eq!(cached.preview, uncached.preview);
        assert_eq!(route.segments.len(), settings.max_segments);
        assert!(route.final_rmse < route.initial_rmse);
        assert_eq!(route.path[0], settings.start_anchor);
        assert_eq!(route.path.len(), route.segments.len() + 1);
        for (anchors, segment) in route.path.windows(2).zip(&route.segments) {
            assert_eq!(anchors, [segment.from, segment.to]);
            let gap = segment.from.abs_diff(segment.to);
            assert!(gap.min(settings.anchors - gap) >= settings.min_anchor_gap);
            assert!(segment.length_mm > 0.0 && segment.length_mm <= settings.diameter_mm + 1e-9);
        }
        let length: f64 = route.segments.iter().map(|segment| segment.length_mm).sum();
        assert!((route.total_length_m * 1000.0 - length).abs() < 1e-8);
    }

    #[test]
    fn white_target_stops_without_adding_thread() {
        let source = DynamicImage::ImageLuma8(GrayImage::from_pixel(64, 64, Luma([255])));
        let settings = Settings {
            anchors: 32,
            raster_size: 64,
            ..Settings::default()
        };
        let art = generate(&source, &settings, |_| ControlFlow::Continue(())).unwrap();
        assert_eq!(art.route.stop_reason, StopReason::NoImprovingMove);
        assert_eq!(art.route.path, [0]);
        assert!(art.route.segments.is_empty());
        assert_eq!(art.preview, art.target);
    }
}
