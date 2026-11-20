//! One-shot observation of an explicit Niri window. The caller must hold the
//! observation policy read lease through delivery. This frame is observation
//! only and must never be entered into the output-local pointer target cache.
use super::{begin_acquisition, bounded_command, check_epoch, State, R};
use std::{io::Cursor, sync::atomic::Ordering, time::Duration};

const MAX_EDGE: u32 = 1920;
const MAX_PNG_BYTES: usize = 4 * 1024 * 1024;

pub(crate) struct WindowPixels {
    pub(crate) width: u32,
    pub(crate) height: u32,
    pub(crate) png: Vec<u8>,
}

pub(crate) fn capture(state: &State, window_id: u64, epoch: u64) -> R<WindowPixels> {
    let check = || -> R<()> {
        state.observation_policy.require_enabled()?;
        check_epoch(state, Some(epoch))?;
        if state.takeover.load(Ordering::SeqCst) {
            return Err("Window capture stopped by takeover".into());
        }
        Ok(())
    };
    check()?;
    if window_id == 0 {
        return Err("Window capture requires a mapped Niri window ID".into());
    }
    let result = {
        let _acquiring = begin_acquisition(state)?;
        check()?;
        bounded_command(
            state,
            "niri",
            &[
                "msg".into(),
                "window-pixels".into(),
                "--id".into(),
                window_id.to_string(),
            ],
            None,
            Duration::from_secs(5),
            Some(epoch),
        )
    };
    check()?;
    let png = result?;
    if png.len() > MAX_PNG_BYTES {
        return Err("Window PNG exceeds its encoded size limit".into());
    }
    // Niri's encoder emits a single 8-bit RGBA image. Validate the actual
    // dimensions before handing its already encoded bytes to the MCP adapter.
    let reader = png::Decoder::new(Cursor::new(&png))
        .read_info()
        .map_err(|_| "Invalid window PNG")?;
    let info = reader.info();
    let (width, height) = (info.width, info.height);
    if !(1..=MAX_EDGE).contains(&width)
        || !(1..=MAX_EDGE).contains(&height)
        || info.color_type != png::ColorType::Rgba
        || info.bit_depth != png::BitDepth::Eight
        || info.animation_control.is_some()
    {
        return Err("Unexpected window PNG format or dimensions".into());
    }
    drop(reader);
    check()?;
    Ok(WindowPixels { width, height, png })
}
