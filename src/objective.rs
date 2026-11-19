//! Score the rendered thread at pixel resolution and at viewing distance.
use crate::{Lines, raster::Canvas};

const CELL_SIDE: usize = 4;
const TONE_WEIGHT: f64 = 0.85;

pub(super) struct Objective {
    pub current: Vec<f32>,
    cells: Vec<usize>,
    depth: Vec<f64>,
    opaque: Vec<u32>,
    weights: Vec<f32>,
    cell_weights: Vec<f64>,
    inverse_counts: Vec<f64>,
    // Sum of target minus render within each cell, excluding the circular border.
    residual: Vec<f64>,
}

pub(super) struct Scratch {
    delta: Vec<f64>,
    touched: Vec<usize>,
}

impl Objective {
    pub fn new(canvas: &Canvas, target: &[f32], emphasis: f32) -> Self {
        // Normalize the local mean by the circle mask so the rim is not a false edge.
        let image = canvas.render(target);
        let mask = image::GrayImage::from_fn(canvas.size as u32, canvas.size as u32, |x, y| {
            image::Luma([if canvas.inside[y as usize * canvas.size + x as usize] {
                255
            } else {
                0
            }])
        });
        let radius = canvas.size as f32 / 48.0;
        let blurred = image::imageops::blur(&image, radius);
        let coverage = image::imageops::blur(&mask, radius);
        let weights: Vec<_> = target
            .iter()
            .zip(blurred.as_raw())
            .zip(coverage.as_raw())
            .map(|((&tone, &light), &area)| {
                let mean = f32::from(255 - light) / f32::from(area.max(1));
                1.0 + emphasis * ((tone - mean).abs() * 5.0).min(2.0).powi(2)
            })
            .collect();
        let cols = canvas.size.div_ceil(CELL_SIDE);
        let mut cell_weights = vec![0.0; cols * cols];
        let mut counts = vec![0usize; cols * cols];
        let mut residual = vec![0.0; counts.len()];
        let mut cells = Vec::with_capacity(target.len());
        for (index, &tone) in target.iter().enumerate() {
            let cell = (index / canvas.size / CELL_SIDE) * cols + index % canvas.size / CELL_SIDE;
            cells.push(cell);
            if canvas.inside[index] {
                counts[cell] += 1;
                cell_weights[cell] += f64::from(weights[index]);
                residual[cell] += f64::from(tone);
            }
        }
        for (weight, &n) in cell_weights.iter_mut().zip(&counts) {
            *weight /= n.max(1) as f64;
        }
        Self {
            depth: vec![0.0; target.len()],
            opaque: vec![0; target.len()],
            weights,
            cell_weights,
            current: vec![0.0; target.len()],
            cells,
            residual,
            inverse_counts: counts.into_iter().map(|n| 1.0 / n.max(1) as f64).collect(),
        }
    }

    pub fn scratch(&self) -> Scratch {
        Scratch {
            delta: vec![0.0; self.residual.len()],
            touched: Vec::new(),
        }
    }

    pub fn gain(
        &self,
        canvas: &Canvas,
        lines: &Lines,
        line: usize,
        target: &[f32],
        opacity: f32,
        scratch: &mut Scratch,
    ) -> f64 {
        let mut fine = 0.0;
        lines.visit(canvas, line, |sample| {
            let i = sample.index as usize;
            let delta = (1.0 - self.current[i]) * (sample.coverage * opacity);
            if delta > 0.0 {
                fine += f64::from(
                    self.weights[i] * delta * (2.0 * (target[i] - self.current[i]) - delta),
                );
                let cell = self.cells[i];
                if scratch.delta[cell] == 0.0 {
                    scratch.touched.push(cell);
                }
                scratch.delta[cell] += f64::from(delta);
            }
        });
        let mut tone = 0.0;
        for cell in scratch.touched.drain(..) {
            let delta = std::mem::take(&mut scratch.delta[cell]);
            tone += self.cell_weights[cell]
                * self.inverse_counts[cell]
                * delta
                * (2.0 * self.residual[cell] - delta);
        }
        fine * (1.0 - TONE_WEIGHT) + tone * TONE_WEIGHT
    }

    // Optical depths add. Keep fully opaque hits separately so removing one
    // chord also works when another chord still blacks out the same pixel.
    pub fn change(&mut self, canvas: &Canvas, lines: &Lines, line: usize, opacity: f32, add: bool) {
        lines.visit(canvas, line, |sample| {
            let i = sample.index as usize;
            let alpha = f64::from(sample.coverage * opacity);
            if alpha >= 1.0 {
                if add {
                    self.opaque[i] += 1;
                } else {
                    self.opaque[i] -= 1;
                }
            } else {
                let log_transmission = (-alpha).ln_1p();
                self.depth[i] += if add {
                    -log_transmission
                } else {
                    log_transmission
                };
            }
            let darkness = if self.opaque[i] > 0 {
                1.0
            } else {
                -(-self.depth[i].max(0.0)).exp_m1() as f32
            };
            self.residual[self.cells[i]] -= f64::from(darkness) - f64::from(self.current[i]);
            self.current[i] = darkness;
        });
    }
}
