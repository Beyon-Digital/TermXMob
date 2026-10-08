//! Physical window geometry; independent of an OS window for source-level tests.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WorkArea {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct SavedWindow {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale: f64,
    pub frame_width: u32,
    pub frame_height: u32,
    pub previous_area: Option<WorkArea>,
}

#[derive(Debug, PartialEq)]
pub struct RestoredWindow {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub minimum_width: u32,
    pub minimum_height: u32,
    pub monitor: Option<usize>,
}

fn scale(value: f64) -> f64 {
    if value.is_finite() && (0.25..=8.0).contains(&value) {
        value
    } else {
        1.0
    }
}

fn overlap(saved: SavedWindow, area: WorkArea) -> u64 {
    let left = i64::from(saved.x).max(i64::from(area.x));
    let top = i64::from(saved.y).max(i64::from(area.y));
    let right = (i64::from(saved.x) + i64::from(saved.width) + i64::from(saved.frame_width))
        .min(i64::from(area.x) + i64::from(area.width));
    let bottom = (i64::from(saved.y) + i64::from(saved.height) + i64::from(saved.frame_height))
        .min(i64::from(area.y) + i64::from(area.height));
    right.saturating_sub(left).max(0) as u64 * bottom.saturating_sub(top).max(0) as u64
}

/// Select one monitor, preserve logical size across DPI changes, then fit the
/// entire decorated window in its work area so its title bar is reachable.
pub fn recover(
    saved: SavedWindow,
    areas: &[WorkArea],
    preferred: Option<usize>,
    primary: Option<usize>,
) -> RestoredWindow {
    let valid = |index: &usize| {
        areas
            .get(*index)
            .is_some_and(|area| area.width > 0 && area.height > 0)
    };
    let intersecting = areas
        .iter()
        .enumerate()
        .filter(|(index, _)| valid(index))
        .map(|(index, area)| (index, overlap(saved, *area)))
        .filter(|(_, amount)| *amount > 0)
        .max_by_key(|(_, amount)| *amount)
        .map(|(index, _)| index);
    let monitor = preferred
        .filter(valid)
        .or(intersecting)
        .or(primary.filter(valid))
        .or_else(|| {
            areas
                .iter()
                .position(|area| area.width > 0 && area.height > 0)
        });
    let area = monitor.map(|index| areas[index]).unwrap_or(WorkArea {
        x: 0,
        y: 0,
        width: 1600,
        height: 1000,
        scale: 1.0,
    });
    let target_scale = scale(area.scale);
    let ratio = target_scale / scale(saved.scale);
    let pixels = |value: u32| {
        (f64::from(value) * ratio)
            .round()
            .clamp(0.0, f64::from(u32::MAX)) as u32
    };
    // Even a tiny work area must retain room for a title bar and content.
    let frame_width = pixels(saved.frame_width).min(area.width.saturating_sub(1));
    let frame_height = pixels(saved.frame_height).min(area.height.saturating_sub(1));
    let available_width = area.width.saturating_sub(frame_width).max(1);
    let available_height = area.height.saturating_sub(frame_height).max(1);
    let minimum_width = (760.0 * target_scale).round().max(1.0) as u32;
    let minimum_height = (520.0 * target_scale).round().max(1.0) as u32;
    let minimum_width = minimum_width.min(available_width);
    let minimum_height = minimum_height.min(available_height);
    let width = pixels(saved.width).clamp(minimum_width, available_width);
    let height = pixels(saved.height).clamp(minimum_height, available_height);
    let (x, y) = match (saved.previous_area, preferred.filter(valid), monitor) {
        (Some(previous), Some(expected), Some(actual)) if expected == actual => (
            f64::from(area.x)
                + (f64::from(saved.x) - f64::from(previous.x)) * target_scale
                    / scale(previous.scale),
            f64::from(area.y)
                + (f64::from(saved.y) - f64::from(previous.y)) * target_scale
                    / scale(previous.scale),
        ),
        (_, _, None) => (40.0, 40.0),
        _ if intersecting.is_none() => (
            f64::from(area.x) + 40.0 * target_scale,
            f64::from(area.y) + 40.0 * target_scale,
        ),
        _ => (f64::from(saved.x), f64::from(saved.y)),
    };
    let bound = |value: f64, origin: i32, extent: u32, used: u32| {
        value
            .round()
            .clamp(
                f64::from(origin),
                f64::from(origin) + f64::from(extent.saturating_sub(used)),
            )
            .clamp(f64::from(i32::MIN), f64::from(i32::MAX)) as i32
    };
    RestoredWindow {
        x: bound(x, area.x, area.width, width + frame_width),
        y: bound(y, area.y, area.height, height + frame_height),
        width,
        height,
        minimum_width,
        minimum_height,
        monitor,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn saved(x: i32, y: i32) -> SavedWindow {
        SavedWindow {
            x,
            y,
            width: 1100,
            height: 780,
            scale: 1.0,
            frame_width: 16,
            frame_height: 40,
            previous_area: None,
        }
    }
    fn area(x: i32, y: i32, width: u32, height: u32, scale: f64) -> WorkArea {
        WorkArea {
            x,
            y,
            width,
            height,
            scale,
        }
    }
    fn reachable(window: &RestoredWindow, area: WorkArea, frame_width: u32, frame_height: u32) {
        assert!(window.x >= area.x && window.y >= area.y);
        assert!(
            i64::from(window.x) + i64::from(window.width + frame_width)
                <= i64::from(area.x) + i64::from(area.width)
        );
        assert!(
            i64::from(window.y) + i64::from(window.height + frame_height)
                <= i64::from(area.y) + i64::from(area.height)
        );
    }
    #[test]
    fn one_pixel_right_edge_and_hidden_top_titlebar_are_recovered() {
        let screen = area(0, 24, 1920, 1016, 1.0);
        for (x, y) in [(1919, 50), (-1099, -750), (100, 1039), (i32::MAX, i32::MIN)] {
            reachable(
                &recover(saved(x, y), &[screen], None, Some(0)),
                screen,
                16,
                40,
            );
        }
    }
    #[test]
    fn selected_small_monitor_does_not_borrow_larger_monitor_dimensions() {
        let screens = [area(0, 0, 3840, 2160, 1.0), area(-1280, 0, 1280, 680, 1.0)];
        let result = recover(saved(-1270, 20), &screens, Some(1), Some(0));
        assert_eq!(result.monitor, Some(1));
        assert_eq!(result.height, 640);
        reachable(&result, screens[1], 16, 40);
    }
    #[test]
    fn mixed_dpi_preserves_logical_size_and_monitor_relative_offset() {
        let old = area(-2560, 24, 2560, 1416, 2.0);
        let current = area(1920, 40, 1920, 1040, 1.0);
        let mut value = saved(-2360, 224);
        value.width = 1800;
        value.height = 1200;
        value.scale = 2.0;
        value.frame_width = 32;
        value.frame_height = 80;
        value.previous_area = Some(old);
        let result = recover(value, &[current], Some(0), Some(0));
        assert_eq!(
            (result.x, result.y, result.width, result.height),
            (2020, 140, 900, 600)
        );
        reachable(&result, current, 16, 40);
    }
    #[test]
    fn removed_monitor_uses_primary_work_area_and_empty_inventory_has_safe_defaults() {
        let primary = area(100, 50, 1000, 650, 1.5);
        let result = recover(saved(-9000, 200), &[primary], None, Some(0));
        reachable(&result, primary, 24, 60);
        let fallback = recover(saved(-9000, 200), &[], None, None);
        assert_eq!((fallback.x, fallback.y, fallback.monitor), (40, 40, None));
    }
    #[test]
    fn tiny_work_area_reduces_minimum_size_instead_of_hiding_window_controls() {
        let screen = area(0, 0, 640, 360, 2.0);
        let result = recover(saved(500, 300), &[screen], Some(0), Some(0));
        assert_eq!((result.minimum_width, result.minimum_height), (608, 280));
        reachable(&result, screen, 32, 80);
    }
    #[test]
    fn fresh_main_and_detached_defaults_fit_small_primary_work_area() {
        let screen = area(0, 24, 800, 456, 1.0);
        for (width, height) in [(1280, 820), (1100, 780)] {
            let mut value = saved(0, 0);
            value.width = width;
            value.height = height;
            value.previous_area = Some(screen);
            let result = recover(value, &[screen], Some(0), Some(0));
            reachable(&result, screen, 16, 40);
            assert_eq!((result.minimum_width, result.minimum_height), (760, 416));
        }
    }
}
