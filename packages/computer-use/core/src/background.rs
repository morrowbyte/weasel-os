//! AT-SPI addresses objects in an application, while Niri addresses windows.
//! Only a unique mapping may authorize a window-scoped batch.
use super::*;

pub(super) fn exact_window(windows: &Value, id: u64) -> R<&Value> {
    let all = windows.as_array().ok_or("Niri window inventory unavailable")?;
    let mut matches = all.iter().filter(|w| w["id"].as_u64() == Some(id));
    let window = matches.next().ok_or("Requested Niri window disappeared")?;
    if matches.next().is_some() || all.iter().any(|other| {
        other["id"] != id && other["pid"] == window["pid"] && other["title"] == window["title"]
    }) {
        return Err("Ambiguous Niri window identity or PID/title mapping".into());
    }
    Ok(window)
}

pub(super) fn observation(
    state: &State,
    window: &Value,
    epoch: u64,
    input_generation: u64,
    at: Instant,
) -> R<Observation> {
    let id = window["id"].as_u64().ok_or("Missing window ID")?;
    let workspace = window["workspace_id"].as_u64().ok_or("Window has no workspace")?;
    let workspaces = niri_epoch(state, "workspaces", Some(epoch))?;
    let mut matches = workspaces.as_array().ok_or("Workspace inventory unavailable")?
        .iter().filter(|w| w["id"].as_u64() == Some(workspace));
    let output = matches.next().and_then(|w| w["output"].as_str()).ok_or("Workspace has no output")?;
    let geometry = niri_epoch(state, "outputs", Some(epoch))?[output]["logical"].clone();
    if matches.next().is_some() || !geometry.is_object() {
        return Err("Window has no unique workspace on an enabled output".into());
    }
    Ok(Observation {
        background_window: Some(id),
        id: format!("ax-obs-{}-{}", state.session_id, state.serial.fetch_add(1, Ordering::SeqCst)),
        at, epoch, input_generation,
        blind: false, blind_task: None,
        observation_generation: state.observation_policy.generation(),
        output: output.into(), output_geometry: geometry,
        windows: json!([window]), focused_window: None, focus_output: None,
        image_width: 0, image_height: 0, image: PathBuf::new(), view_image: PathBuf::new(),
        view: Crop { x: 0, y: 0, width: 0, height: 0 },
    })
}

pub(super) fn batch_window(state: &State, obs: &Observation, actions: &[Action]) -> R<Option<u64>> {
    let targets = state.semantic_targets.lock().map_err(|_| "Semantic handle cache poisoned")?;
    let mut background_id = None;
    let mut shared_seat = false;
    for action in actions {
        let target = match action {
            Action::SemanticClick { handle_id, .. } | Action::SemanticSetValue { handle_id, .. } =>
                targets.get(handle_id).filter(|t| t.background),
            _ => None,
        };
        if let Some(target) = target {
            let id = target.window["id"].as_u64().ok_or("Missing background window ID")?;
            if background_id.replace(id).is_some_and(|prior| prior != id) {
                return Err("A background batch must address one window".into());
            }
        } else {
            shared_seat = true;
        }
    }
    if (shared_seat && background_id.is_some())
        || obs.background_window.is_some_and(|id| Some(id) != background_id) {
        return Err("A window observation permits only background click/edit on its exact window".into());
    }
    Ok(background_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ambiguous_niri_to_atspi_mapping_is_refused() {
        let w = json!({"id":1,"pid":12,"title":"document"});
        for other in [json!({"id":2,"pid":12,"title":"document"}), w.clone()] {
            assert!(exact_window(&json!([w, other]), 1).is_err());
        }
        assert_eq!(exact_window(&json!([w, {"id":2,"pid":12,"title":"other"}]), 1).unwrap(), &w);
    }
}
