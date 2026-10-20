//! Deterministic desktop movement in native desktop coordinates. No OS handles,
//! agent credentials, window titles, or screen pixels enter this simulation.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}
impl Rect {
    pub fn right(self) -> f64 {
        self.x + self.width
    }
    pub fn bottom(self) -> f64 {
        self.y + self.height
    }
    fn valid(self) -> bool {
        [self.x, self.y, self.width, self.height]
            .iter()
            .all(|n| n.is_finite())
            && self.width > 0.0
            && self.height > 0.0
    }
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Surface {
    pub left: f64,
    pub right: f64,
    pub y: f64,
}
#[derive(Clone, Debug, Default)]
pub struct Habitat {
    pub areas: Vec<(Rect, f64)>,
    pub surfaces: Vec<Surface>,
}
impl Habitat {
    /// Windows arrive front to back. Subtract every covering foreground window
    /// from each lower window's top edge, then clip to usable monitor areas.
    pub fn new(areas: Vec<(Rect, f64)>, windows: &[Rect]) -> Self {
        let areas: Vec<_> = areas
            .into_iter()
            .filter(|(r, s)| r.valid() && s.is_finite() && *s > 0.0)
            .collect();
        let mut surfaces = Vec::new();
        for (index, window) in windows.iter().enumerate().filter(|(_, r)| r.valid()) {
            for (area, _) in &areas {
                if window.y < area.y + 40.0 || window.y > area.bottom() {
                    continue;
                }
                let mut spans = vec![(window.x.max(area.x), window.right().min(area.right()))];
                for cover in windows[..index]
                    .iter()
                    .filter(|r| r.valid() && r.y <= window.y && r.bottom() >= window.y - 1.0)
                {
                    spans = spans
                        .into_iter()
                        .flat_map(|(l, r)| {
                            if cover.right() <= l || cover.x >= r {
                                vec![(l, r)]
                            } else {
                                vec![(l, cover.x.min(r)), (cover.right().max(l), r)]
                            }
                        })
                        .filter(|(l, r)| r - l >= 32.0)
                        .collect();
                }
                surfaces.extend(spans.into_iter().filter(|(l, r)| r - l >= 32.0).map(
                    |(left, right)| Surface {
                        left,
                        right,
                        y: window.y,
                    },
                ));
            }
        }
        surfaces.extend(areas.iter().map(|(r, _)| Surface {
            left: r.x,
            right: r.right(),
            y: r.bottom(),
        }));
        Self { areas, surfaces }
    }
    pub fn scale_at(&self, x: f64, y: f64) -> f64 {
        self.areas
            .iter()
            .find(|(r, _)| x >= r.x && x <= r.right() && y >= r.y && y <= r.bottom())
            .map(|(_, s)| *s)
            .unwrap_or_else(|| self.areas.first().map(|(_, scale)| *scale).unwrap_or(1.0))
    }
}
#[derive(Clone, Debug)]
struct Hop {
    from_x: f64,
    from_y: f64,
    to_x: f64,
    to_y: f64,
    age: f64,
    duration: f64,
    lift: f64,
}
#[derive(Clone, Debug)]
pub struct Body {
    pub x: f64,
    pub y: f64,
    pub direction: f64,
    pub moving: bool,
    pub falling: bool,
    pub hopping: bool,
    pub dragging: bool,
    pub paused: bool,
    pub hidden: bool,
    velocity_y: f64,
    age: f64,
    seed: usize,
    hop: Option<Hop>,
    next_hop: f64,
}
impl Body {
    pub fn new(seed: usize) -> Self {
        Self {
            x: f64::NAN,
            y: f64::NAN,
            direction: if seed % 2 == 0 { 1.0 } else { -1.0 },
            moving: false,
            falling: false,
            hopping: false,
            dragging: false,
            paused: false,
            hidden: false,
            velocity_y: 0.0,
            age: seed as f64 * 1.7,
            seed,
            hop: None,
            next_hop: 12.0 + seed as f64 * 3.0,
        }
    }
    pub fn home(&mut self, habitat: &Habitat, size: f64) {
        if let Some((area, _)) = habitat.areas.first() {
            self.x =
                (area.right() - size * (self.seed as f64 + 1.0) - 24.0).max(area.x + size / 2.0);
            self.y = area.bottom();
            self.velocity_y = 0.0;
            self.falling = false;
            self.hop = None;
            self.hopping = false;
        }
    }
    /// Coordinates describe the foot anchor. A moved/removed support falls to
    /// the next exposed edge. Monitor changes recover the entire pet first.
    pub fn tick(
        &mut self,
        habitat: &Habitat,
        dt: f64,
        size: f64,
        active: bool,
        reduced: bool,
        playful: bool,
    ) {
        self.moving = false;
        if habitat.areas.is_empty() || self.dragging || self.hidden {
            return;
        }
        let fits = habitat.areas.iter().any(|(r, _)| {
            self.x >= r.x + size / 2.0
                && self.x <= r.right() - size / 2.0
                && self.y >= r.y + size
                && self.y <= r.bottom() + 1.0
        });
        if !fits || !self.x.is_finite() || !self.y.is_finite() {
            self.home(habitat, size);
        }
        if self.paused {
            return;
        }
        if reduced {
            // Settle instantly on a safe support when motion is disabled.
            self.hop = None;
            self.hopping = false;
            self.falling = false;
            if let Some(surface) = habitat
                .surfaces
                .iter()
                .filter(|s| {
                    s.y >= self.y && self.x >= s.left + size / 2.0 && self.x <= s.right - size / 2.0
                })
                .min_by(|a, b| a.y.total_cmp(&b.y))
            {
                self.y = surface.y;
            }
            return;
        }
        let dt = dt.clamp(0.0, 0.05);
        self.age += dt;
        if let Some(mut hop) = self.hop.take() {
            let valid = habitat.surfaces.iter().any(|s| {
                (s.y - hop.to_y).abs() < 2.0
                    && hop.to_x >= s.left + size / 2.0
                    && hop.to_x <= s.right - size / 2.0
            });
            if valid {
                hop.age += dt;
                let t = (hop.age / hop.duration).min(1.0);
                let ease = t * t * (3.0 - 2.0 * t);
                self.x = hop.from_x + (hop.to_x - hop.from_x) * ease;
                self.y = hop.from_y + (hop.to_y - hop.from_y) * ease
                    - (std::f64::consts::PI * t).sin() * hop.lift;
                self.hopping = t < 1.0;
                self.falling = false;
                if t < 1.0 {
                    self.hop = Some(hop);
                } else {
                    self.velocity_y = 0.0;
                }
                return;
            }
            self.hopping = false;
            self.velocity_y = 0.0;
        }
        if !active && self.age >= self.next_hop {
            self.next_hop = self.age + if playful { 10.0 } else { 24.0 };
            self.approach(habitat, size);
            if playful && self.hop.is_none() {
                self.hop_in_place(habitat, size);
            }
            if self.hop.is_some() {
                return;
            }
        }
        let support = habitat
            .surfaces
            .iter()
            .find(|s| {
                (s.y - self.y).abs() < 2.0
                    && self.x >= s.left + size / 2.0
                    && self.x <= s.right - size / 2.0
            })
            .copied();
        if let Some(surface) = support {
            self.falling = false;
            self.velocity_y = 0.0;
            let cycle = if playful { 8.0 } else { 16.0 };
            self.moving = !active && self.age % cycle > cycle * 0.55;
            if self.moving {
                let speed = if playful { 44.0 } else { 27.0 };
                self.x += self.direction * speed * dt;
                if self.x <= surface.left + size / 2.0 {
                    self.x = surface.left + size / 2.0;
                    self.direction = 1.0;
                }
                if self.x >= surface.right - size / 2.0 {
                    self.x = surface.right - size / 2.0;
                    self.direction = -1.0;
                }
            }
        } else {
            self.falling = true;
            self.velocity_y = (self.velocity_y + 600.0 * dt).min(500.0);
            let next = self.y + self.velocity_y * dt;
            if let Some(surface) = habitat
                .surfaces
                .iter()
                .filter(|s| {
                    s.y >= self.y
                        && s.y <= next
                        && self.x >= s.left + size / 2.0
                        && self.x <= s.right - size / 2.0
                })
                .min_by(|a, b| a.y.total_cmp(&b.y))
            {
                self.y = surface.y;
                self.velocity_y = 0.0;
                self.falling = false;
            } else {
                self.y = next;
            }
        }
    }
    pub fn approach(&mut self, habitat: &Habitat, size: f64) {
        if self.hop.is_some() || self.dragging || self.paused || !self.x.is_finite() {
            return;
        }
        // A target must be visible, close enough to reach, and on the same
        // usable monitor. Never teleport to a remote/covered desktop location.
        let target = habitat
            .surfaces
            .iter()
            .filter_map(|surface| {
                if surface.right - surface.left < size
                    || (surface.y - self.y).abs() > size * 2.5
                    || (surface.y - self.y).abs() < 4.0
                {
                    return None;
                }
                let x = self
                    .x
                    .clamp(surface.left + size / 2.0, surface.right - size / 2.0);
                if (x - self.x).abs() > size * 3.0 {
                    return None;
                }
                let area = habitat
                    .areas
                    .iter()
                    .find(|(r, _)| {
                        self.x >= r.x + size / 2.0
                            && self.x <= r.right() - size / 2.0
                            && x >= r.x + size / 2.0
                            && x <= r.right() - size / 2.0
                            && surface.y >= r.y + size
                            && surface.y <= r.bottom()
                    })?
                    .0;
                let distance = (x - self.x).hypot(surface.y - self.y);
                Some((distance, x, surface.y, area))
            })
            .min_by(|a, b| a.0.total_cmp(&b.0));
        if let Some((distance, x, y, area)) = target {
            let lift = (size * 0.65).min(self.y.min(y) - area.y - size).max(0.0);
            self.direction = if x < self.x { -1.0 } else { 1.0 };
            self.hop = Some(Hop {
                from_x: self.x,
                from_y: self.y,
                to_x: x,
                to_y: y,
                age: 0.0,
                duration: (distance / 220.0).clamp(0.5, 1.4),
                lift,
            });
            self.hopping = true;
            self.moving = false;
            self.falling = false;
        }
    }
    pub fn hop_in_place(&mut self, habitat: &Habitat, size: f64) {
        if self.hop.is_some() || self.dragging || self.paused || !self.x.is_finite() {
            return;
        }
        if let Some((area, _)) = habitat.areas.iter().find(|(r, _)| {
            self.x >= r.x && self.x <= r.right() && self.y >= r.y + size && self.y <= r.bottom()
        }) {
            let lift = (size * 0.6).min(self.y - area.y - size).max(0.0);
            if lift > 0.0 {
                self.hop = Some(Hop {
                    from_x: self.x,
                    from_y: self.y,
                    to_x: self.x,
                    to_y: self.y,
                    age: 0.0,
                    duration: 0.65,
                    lift,
                });
                self.hopping = true;
                self.moving = false;
                self.falling = false;
            }
        }
    }
    pub fn idle_pose(&self) -> &'static str {
        if self.age % 48.0 > 39.0 {
            "sleeping"
        } else if self.age % 24.0 > 8.0 && self.age % 24.0 < 13.0 {
            "sit"
        } else {
            "idle"
        }
    }
    pub fn stop_drag(&mut self) {
        self.hop = None;
        self.hopping = false;
        self.velocity_y = 0.0;
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn area(x: f64, y: f64, w: f64, h: f64) -> Rect {
        Rect {
            x,
            y,
            width: w,
            height: h,
        }
    }
    #[test]
    fn exposed_edges_subtract_foreground_stacking() {
        let h = Habitat::new(
            vec![(area(0.0, 0.0, 1000.0, 700.0), 1.0)],
            &[
                area(200.0, 100.0, 300.0, 400.0),
                area(100.0, 200.0, 800.0, 400.0),
            ],
        );
        assert!(h.surfaces.contains(&Surface {
            left: 100.0,
            right: 200.0,
            y: 200.0
        }));
        assert!(h.surfaces.contains(&Surface {
            left: 500.0,
            right: 900.0,
            y: 200.0
        }));
        assert!(!h
            .surfaces
            .iter()
            .any(|s| s.y == 200.0 && s.left < 300.0 && s.right > 300.0));
    }
    #[test]
    fn removed_monitor_recovers_and_mixed_scale_is_local() {
        let a = Habitat::new(vec![(area(-1400.0, 0.0, 1400.0, 900.0), 2.0)], &[]);
        let b = Habitat::new(vec![(area(0.0, 24.0, 1000.0, 700.0), 1.0)], &[]);
        assert_eq!(a.scale_at(f64::NAN, f64::NAN), 2.0);
        let mut p = Body::new(0);
        p.home(&a, 96.0);
        assert_eq!(a.scale_at(p.x, p.y), 2.0);
        p.tick(&b, 0.016, 96.0, false, false, false);
        assert!(p.x > 0.0 && p.y == 724.0);
    }
    #[test]
    fn falling_lands_on_next_surface_and_removed_support_falls() {
        let h = Habitat::new(
            vec![(area(0.0, 0.0, 1000.0, 700.0), 1.0)],
            &[area(0.0, 300.0, 900.0, 200.0)],
        );
        let mut p = Body::new(0);
        p.x = 400.0;
        p.y = 150.0;
        for _ in 0..80 {
            p.tick(&h, 1.0 / 60.0, 96.0, true, false, false);
        }
        assert_eq!(p.y, 300.0);
        let empty = Habitat::new(h.areas.clone(), &[]);
        p.tick(&empty, 0.016, 96.0, true, false, false);
        assert!(p.falling && p.y > 300.0);
    }
    #[test]
    fn reduced_motion_and_active_agent_do_not_patrol() {
        let h = Habitat::new(vec![(area(0.0, 0.0, 1000.0, 700.0), 1.0)], &[]);
        let mut p = Body::new(0);
        p.home(&h, 96.0);
        let x = p.x;
        for _ in 0..1200 {
            p.tick(&h, 1.0 / 60.0, 96.0, true, false, true);
        }
        assert_eq!(p.x, x);
        p.tick(&h, 0.016, 96.0, false, true, true);
        assert!(!p.moving);
    }
    #[test]
    fn clipped_narrow_edges_and_invalid_geometry_are_ignored() {
        let h = Habitat::new(
            vec![(area(-600.0, 0.0, 600.0, 600.0), 1.0)],
            &[
                area(-610.0, 200.0, 20.0, 300.0),
                area(f64::NAN, 50.0, 100.0, 100.0),
            ],
        );
        assert_eq!(h.surfaces.len(), 1);
    }
    #[test]
    fn approach_interpolates_and_cancels_a_disappearing_target() {
        let h = Habitat::new(
            vec![(area(0.0, 0.0, 1000.0, 700.0), 1.0)],
            &[area(0.0, 500.0, 1000.0, 100.0)],
        );
        let mut p = Body::new(0);
        p.home(&h, 96.0);
        let origin = (p.x, p.y);
        p.approach(&h, 96.0);
        assert_eq!((p.x, p.y), origin);
        assert!(p.hopping);
        p.tick(&h, 0.05, 96.0, true, false, false);
        assert!(p.y < 700.0 && p.y > 500.0);
        let empty = Habitat::new(h.areas.clone(), &[]);
        p.tick(&empty, 0.05, 96.0, true, false, false);
        assert!(!p.hopping && p.falling);
    }
    #[test]
    fn idle_explores_nearby_window_edge_and_reduced_motion_settles() {
        let h = Habitat::new(
            vec![(area(0.0, 0.0, 1000.0, 700.0), 1.0)],
            &[area(0.0, 500.0, 1000.0, 100.0)],
        );
        let mut p = Body::new(0);
        p.home(&h, 96.0);
        for _ in 0..900 {
            p.tick(&h, 1.0 / 60.0, 96.0, false, false, true);
        }
        assert_eq!(p.y, 500.0);
        p.approach(&h, 96.0);
        assert!(p.hopping);
        p.tick(&h, 0.016, 96.0, false, true, true);
        assert!(!p.hopping && !p.falling);
    }
}
