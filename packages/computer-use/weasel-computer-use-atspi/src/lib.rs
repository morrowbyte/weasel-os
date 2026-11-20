//! Direct AT-SPI object identity prototype, compile-only until live acceptance.
//! Protocol: GNOME at-spi2-core XML Accessible/Action/EditableText interfaces.
//! There is no coordinate, index-token, input, or insertion fallback.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    time::{Duration, Instant},
};
use zbus::{
    blocking::{connection::Builder, Connection},
    zvariant::{OwnedObjectPath, OwnedValue},
};
type R<T> = Result<T, String>;
const ACCESSIBLE: &str = "org.a11y.atspi.Accessible";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
pub struct Object {
    pub bus: String,
    pub path: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Node {
    pub object: Object,
    pub parent: Option<Object>,
    pub depth: usize,
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub role: String,
    pub role_id: u32,
    pub interfaces: Vec<String>,
    pub states: Vec<u32>,
    pub protected: bool,
    pub action_names: Vec<String>,
    pub text_excerpt: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text_selection: Option<TextSelection>,
}
/// Selection evidence is a bounded readback, not an atomic text snapshot or input authority.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TextSelection {
    pub status: String,
    pub character_count: Option<i32>,
    pub selection_count: Option<i32>,
    pub ranges: Vec<SelectionRange>,
    pub full_text_selected: Option<bool>,
    pub stable_readback: bool,
    pub offset_unit: String,
    pub end_exclusive: bool,
    pub text_identity_verified: bool,
    pub reason: Option<String>,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct SelectionRange {
    pub start: i32,
    pub end: i32,
}
#[derive(Clone, Copy, Debug)]
enum SelectionReadError {
    Unsupported,
    Failed,
}
type SelectionResult<T> = Result<T, SelectionReadError>;
impl TextSelection {
    fn unavailable(status: &str, reason: &str) -> Self {
        Self {
            status: status.into(),
            character_count: None,
            selection_count: None,
            ranges: Vec::new(),
            full_text_selected: None,
            stable_readback: false,
            offset_unit: "unicode_codepoints".into(),
            end_exclusive: true,
            text_identity_verified: false,
            reason: Some(reason.into()),
        }
    }
    fn read_error(error: SelectionReadError, stage: &str) -> Self {
        Self::unavailable(
            match error {
                SelectionReadError::Unsupported => "unsupported",
                SelectionReadError::Failed => "error",
            },
            stage,
        )
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Snapshot {
    pub pid: u32,
    pub requested_title: String,
    pub application: Object,
    pub window: Object,
    pub nodes: Vec<Node>,
    pub complete: bool,
    pub visible_modals: Vec<Object>,
    pub semantic_scope: String,
    pub sole_window_fallback_allowed: bool,
    pub bus_guid: String,
    #[serde(default = "default_max_nodes")]
    pub max_nodes: usize,
    #[serde(default = "default_max_depth")]
    pub max_depth: usize,
    #[serde(default)]
    pub visibility_traversal: VisibilityTraversal,
    /// None means the older producer supplied no diagnostic evidence.
    #[serde(default)]
    pub traversal_diagnostics: Option<TraversalDiagnostics>,
}

const OBSERVE_DEADLINE: Duration = Duration::from_secs(3);
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct IncompleteReasons {
    pub window_discovery_deadline_cutoffs: usize,
    pub window_discovery_read_errors: usize,
    pub traversal_deadline_cutoffs: usize,
    pub repeated_object_references: usize,
    pub node_read_or_state_errors: usize,
    pub child_enumeration_errors: usize,
    pub child_visibility_probe_errors: usize,
    pub showing_child_under_hidden_parent: usize,
    pub selected_root_not_showing: usize,
    pub max_nodes_cutoffs: usize,
    pub max_depth_cutoffs: usize,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TraversalDiagnostics {
    pub revision: u32,
    pub effective_max_nodes: usize,
    pub effective_max_depth: usize,
    /// Elapsed-time checks at existing discovery/traversal checkpoints;
    /// individual D-Bus calls can finish after this soft boundary.
    pub effective_deadline_ms: u64,
    /// Counts observed refusal/cutoff checks, not omitted nodes or descendants.
    pub incomplete_reasons: IncompleteReasons,
}
const VISIBLE_TRAVERSAL: &str = "selected-window-showing-ancestor-chain-v1";
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct VisibilityTraversal {
    /// An empty value denotes the older all-descendants snapshot protocol.
    pub definition: String,
    pub selected_root_showing: bool,
    pub pruned_not_showing_branches: usize,
    pub pruned_protected_branches: usize,
    pub immediate_child_visibility_probes: usize,
    pub visibility_probe_errors: usize,
    pub showing_child_under_hidden_parent: usize,
    /// Pruning deliberately does not enumerate or estimate omitted descendants.
    pub omitted_descendant_count: Option<usize>,
}
fn default_max_nodes() -> usize {
    1000
}
fn default_max_depth() -> usize {
    40
}

fn connection() -> R<Connection> {
    let session = Builder::session()
        .map_err(|e| e.to_string())?
        .method_timeout(Duration::from_millis(250))
        .build()
        .map_err(|e| e.to_string())?;
    let msg = session
        .call_method(
            Some("org.a11y.Bus"),
            "/org/a11y/bus",
            Some("org.a11y.Bus"),
            "GetAddress",
            &(),
        )
        .map_err(|e| format!("AT-SPI bus unavailable: {e}"))?;
    let address: String = msg.body().deserialize().map_err(|e| e.to_string())?;
    Builder::address(address.as_str())
        .map_err(|e| e.to_string())?
        .method_timeout(Duration::from_millis(200))
        .build()
        .map_err(|e| e.to_string())
}
fn property(conn: &Connection, obj: &Object, name: &str) -> R<OwnedValue> {
    // Explicit Get avoids GetAll/property-cache crashes in legacy Qt bridges.
    conn.call_method(
        Some(obj.bus.as_str()),
        obj.path.as_str(),
        Some("org.freedesktop.DBus.Properties"),
        "Get",
        &(ACCESSIBLE, name),
    )
    .map_err(|e| e.to_string())?
    .body()
    .deserialize()
    .map_err(|e| e.to_string())
}
fn string_call(conn: &Connection, obj: &Object, interface: &str, method: &str) -> R<String> {
    conn.call_method(
        Some(obj.bus.as_str()),
        obj.path.as_str(),
        Some(interface),
        method,
        &(),
    )
    .map_err(|e| e.to_string())?
    .body()
    .deserialize()
    .map_err(|e| e.to_string())
}
fn owner_pid(conn: &Connection, bus: &str) -> R<u32> {
    conn.call_method(
        Some("org.freedesktop.DBus"),
        "/org/freedesktop/DBus",
        Some("org.freedesktop.DBus"),
        "GetConnectionUnixProcessID",
        &(bus,),
    )
    .map_err(|e| e.to_string())?
    .body()
    .deserialize()
    .map_err(|e| e.to_string())
}
fn unique_owner(conn: &Connection, bus: &str) -> R<String> {
    if bus.starts_with(':') {
        return Ok(bus.into());
    }
    conn.call_method(
        Some("org.freedesktop.DBus"),
        "/org/freedesktop/DBus",
        Some("org.freedesktop.DBus"),
        "GetNameOwner",
        &(bus,),
    )
    .map_err(|e| e.to_string())?
    .body()
    .deserialize()
    .map_err(|e| e.to_string())
}
fn children(conn: &Connection, obj: &Object) -> R<Vec<Object>> {
    let refs: Vec<(String, OwnedObjectPath)> = conn
        .call_method(
            Some(obj.bus.as_str()),
            obj.path.as_str(),
            Some(ACCESSIBLE),
            "GetChildren",
            &(),
        )
        .map_err(|e| e.to_string())?
        .body()
        .deserialize()
        .map_err(|e| e.to_string())?;
    refs.into_iter()
        .filter(|(bus, path)| !bus.is_empty() && path.as_str() != "/org/a11y/atspi/null")
        .map(|(bus, path)| {
            unique_owner(conn, &bus).map(|bus| Object {
                bus,
                path: path.to_string(),
            })
        })
        .collect()
}
fn state(states: &[u32], bit: usize) -> bool {
    states
        .get(bit / 32)
        .is_some_and(|v| v & (1 << (bit % 32)) != 0)
}
fn node_states(conn: &Connection, object: &Object) -> R<Vec<u32>> {
    let states: Vec<u32> = conn
        .call_method(
            Some(object.bus.as_str()),
            object.path.as_str(),
            Some(ACCESSIBLE),
            "GetState",
            &(),
        )
        .map_err(|e| e.to_string())?
        .body()
        .deserialize()
        .map_err(|e| e.to_string())?;
    if states.is_empty() {
        return Err("AT-SPI GetState returned no state words; visibility unknown".into());
    }
    Ok(states)
}
const TEXT: &str = "org.a11y.atspi.Text";
const ACTION: &str = "org.a11y.atspi.Action";
const MAX_ACTIONS: i32 = 64;
const MAX_TEXT_BYTES: usize = 65536;
fn interface_int_property(
    conn: &Connection,
    object: &Object,
    interface: &str,
    name: &str,
) -> R<i32> {
    let value: OwnedValue = conn
        .call_method(
            Some(object.bus.as_str()),
            object.path.as_str(),
            Some("org.freedesktop.DBus.Properties"),
            "Get",
            &(interface, name),
        )
        .map_err(|e| e.to_string())?
        .body()
        .deserialize()
        .map_err(|e| e.to_string())?;
    i32::try_from(value).map_err(|e| e.to_string())
}
// Gecko returns all three GetActions fields through one borrowed string buffer;
// keybindings such as ";;" overwrite the bulk name/description. GetName provides
// machine-readable names and is also the authority at dispatch, never an index
// chosen by a model or a translated bulk label.
fn read_action_names(
    mut count: impl FnMut() -> R<i32>,
    mut name: impl FnMut(i32) -> R<String>,
) -> R<Vec<String>> {
    let before = count()?;
    if !(0..=MAX_ACTIONS).contains(&before) {
        return Err("AT-SPI action count exceeds supported 0..64 bound".into());
    }
    let mut names = Vec::new();
    for index in 0..before {
        let value = name(index)?;
        if value.trim().is_empty() || value.len() > 128 {
            return Err(
                "AT-SPI named action is empty or exceeds supported bound; no invented action"
                    .into(),
            );
        }
        names.push(value);
    }
    if count()? != before {
        return Err("AT-SPI action count changed during named enumeration; observe again".into());
    }
    Ok(names)
}
fn action_names(conn: &Connection, object: &Object) -> R<Vec<String>> {
    read_action_names(
        || interface_int_property(conn, object, ACTION, "NActions"),
        |index| {
            conn.call_method(
                Some(object.bus.as_str()),
                object.path.as_str(),
                Some(ACTION),
                "GetName",
                &(index,),
            )
            .map_err(|e| e.to_string())?
            .body()
            .deserialize()
            .map_err(|e| e.to_string())
        },
    )
}
fn named_action_index(names: &[String], requested: &str) -> R<i32> {
    let matching: Vec<usize> = names
        .iter()
        .enumerate()
        .filter(|(_, n)| n.as_str() == requested)
        .map(|(i, _)| i)
        .collect();
    if matching.len() != 1 {
        return Err("Requested named semantic action is missing or ambiguous; no fallback".into());
    }
    Ok(matching[0] as i32)
}
// AT-SPI offsets count Unicode characters. Gecko returns an empty string when
// an end offset exceeds CharacterCount; this must not masquerade as empty text.
// No unbounded GetText(0,-1), and missing/inconsistent bridge reads fail closed.
fn read_text_bounded(
    mut count: impl FnMut() -> R<i32>,
    mut get: impl FnMut(i32) -> R<String>,
    maximum_characters: usize,
    complete: bool,
) -> R<String> {
    let before = count()?;
    if before < 0 || (complete && before as usize > maximum_characters) {
        return Err("AT-SPI complete text exceeds supported character bound".into());
    }
    let end = (before as usize).min(maximum_characters) as i32;
    let text = get(end)?;
    if text.len() > MAX_TEXT_BYTES || text.chars().count() != end as usize {
        return Err(
            "AT-SPI text range is incomplete or uses unsupported character offsets; observe again"
                .into(),
        );
    }
    if count()? != before {
        return Err("AT-SPI character count changed during text read; observe again".into());
    }
    Ok(text)
}
fn read_text(
    conn: &Connection,
    object: &Object,
    maximum_characters: usize,
    complete: bool,
) -> R<String> {
    read_text_bounded(
        || interface_int_property(conn, object, TEXT, "CharacterCount"),
        |end| {
            conn.call_method(
                Some(object.bus.as_str()),
                object.path.as_str(),
                Some(TEXT),
                "GetText",
                &(0i32, end),
            )
            .map_err(|e| e.to_string())?
            .body()
            .deserialize()
            .map_err(|e| e.to_string())
        },
        maximum_characters,
        complete,
    )
}
fn read_full_text(conn: &Connection, object: &Object) -> R<String> {
    read_text(conn, object, MAX_TEXT_BYTES, true)
}
// At most six calls for one focused Text object: Count/NSelections/range,
// then Count/NSelections/range again. No text content or caret inference.
fn read_selection_bounded(
    mut count: impl FnMut() -> SelectionResult<i32>,
    mut selection_count: impl FnMut() -> SelectionResult<i32>,
    mut selection: impl FnMut(i32) -> SelectionResult<(i32, i32)>,
) -> TextSelection {
    let before = match count() {
        Ok(value) if value >= 0 => value,
        Ok(_) => return TextSelection::unavailable("error", "invalid_character_count"),
        Err(error) => return TextSelection::read_error(error, "character_count_unavailable"),
    };
    let n = match selection_count() {
        Ok(value) if value >= 0 => value,
        Ok(_) => return TextSelection::unavailable("error", "invalid_selection_count"),
        Err(error) => return TextSelection::read_error(error, "selection_count_unavailable"),
    };
    let mut result = TextSelection::unavailable("available", "unverified_readback");
    result.character_count = Some(before);
    result.selection_count = Some(n);
    if n > 1 {
        result.status = "unsupported".into();
        result.reason = Some("multiple_selections_exceed_single_range_bound".into());
        return result;
    }
    let range = if n == 1 {
        match selection(0) {
            Ok((start, end)) if 0 <= start && start <= end && end <= before => {
                Some(SelectionRange { start, end })
            }
            Ok(_) => return TextSelection::unavailable("error", "selection_range_out_of_bounds"),
            Err(error) => return TextSelection::read_error(error, "selection_range_unavailable"),
        }
    } else {
        None
    };
    let after = match count() {
        Ok(value) => value,
        Err(error) => {
            return TextSelection::read_error(error, "character_count_readback_unavailable")
        }
    };
    let after_n = match selection_count() {
        Ok(value) => value,
        Err(error) => {
            return TextSelection::read_error(error, "selection_count_readback_unavailable")
        }
    };
    if after != before || after_n != n {
        return TextSelection::unavailable("error", "selection_changed_during_readback");
    }
    if let Some(range) = range {
        match selection(0) {
            Ok((start, end)) if start == range.start && end == range.end => {}
            Ok(_) => {
                return TextSelection::unavailable("error", "selection_changed_during_readback")
            }
            Err(error) => {
                return TextSelection::read_error(error, "selection_range_readback_unavailable")
            }
        }
        result.full_text_selected = Some(before > 0 && range.start == 0 && range.end == before);
        result.ranges.push(range);
    } else {
        result.full_text_selected = Some(false);
    }
    result.stable_readback = true;
    result.reason = None;
    result
}
fn selection_bus_error(error: zbus::Error) -> SelectionReadError {
    match error {
        zbus::Error::MethodError(name, _, _)
            if matches!(
                name.as_str(),
                "org.freedesktop.DBus.Error.UnknownMethod"
                    | "org.freedesktop.DBus.Error.UnknownInterface"
                    | "org.freedesktop.DBus.Error.NotSupported"
                    | "org.a11y.atspi.Error.NotImplemented"
            ) =>
        {
            SelectionReadError::Unsupported
        }
        _ => SelectionReadError::Failed,
    }
}
fn selection_with_focus_readback(
    mut focused: impl FnMut() -> R<bool>,
    mut read: impl FnMut() -> TextSelection,
) -> TextSelection {
    match focused() {
        Ok(true) => {}
        Ok(false) => {
            return TextSelection::unavailable("error", "text_focus_lost_before_selection")
        }
        Err(_) => {
            return TextSelection::unavailable("error", "text_focus_before_selection_unknown")
        }
    }
    let result = read();
    match focused() {
        Ok(true) => result,
        Ok(false) => TextSelection::unavailable("error", "text_focus_changed_during_selection"),
        Err(_) => TextSelection::unavailable("error", "text_focus_readback_unknown"),
    }
}
fn read_selection(conn: &Connection, object: &Object) -> TextSelection {
    selection_with_focus_readback(
        || {
            node_states(conn, object)
                .map(|states| state(&states, 8) && state(&states, 12) && state(&states, 25))
        },
        || {
            read_selection_bounded(
                || {
                    let value: OwnedValue = conn
                        .call_method(
                            Some(object.bus.as_str()),
                            object.path.as_str(),
                            Some("org.freedesktop.DBus.Properties"),
                            "Get",
                            &(TEXT, "CharacterCount"),
                        )
                        .map_err(selection_bus_error)?
                        .body()
                        .deserialize()
                        .map_err(|_| SelectionReadError::Failed)?;
                    i32::try_from(value).map_err(|_| SelectionReadError::Failed)
                },
                || {
                    conn.call_method(
                        Some(object.bus.as_str()),
                        object.path.as_str(),
                        Some(TEXT),
                        "GetNSelections",
                        &(),
                    )
                    .map_err(selection_bus_error)?
                    .body()
                    .deserialize()
                    .map_err(|_| SelectionReadError::Failed)
                },
                |index| {
                    conn.call_method(
                        Some(object.bus.as_str()),
                        object.path.as_str(),
                        Some(TEXT),
                        "GetSelection",
                        &(index,),
                    )
                    .map_err(selection_bus_error)?
                    .body()
                    .deserialize::<(i32, i32)>()
                    .map_err(|_| SelectionReadError::Failed)
                },
            )
        },
    )
}
// Enrichment never supplies handles or changes tree completeness. Protected
// metadata has zero reads; ambiguous focused objects likewise have zero reads.
fn add_selection_readback(
    nodes: &mut [Node],
    deadline_elapsed: bool,
    mut read: impl FnMut(&Object) -> TextSelection,
) {
    let eligible = |node: &Node| {
        !node.protected
            && node.interfaces.iter().any(|s| s == TEXT)
            && state(&node.states, 8)
            && state(&node.states, 12)
            && state(&node.states, 25)
    };
    let eligible_count = nodes.iter().filter(|node| eligible(node)).count();
    for node in nodes {
        if node.protected {
            node.text_selection = Some(TextSelection::unavailable(
                "unsupported",
                "protected_metadata_not_read",
            ));
        } else if node.interfaces.iter().any(|s| s == TEXT) {
            node.text_selection = Some(if !eligible(node) {
                TextSelection::unavailable("unsupported", "requires_showing_enabled_focused_text")
            } else if eligible_count != 1 {
                TextSelection::unavailable("error", "focused_text_identity_ambiguous")
            } else if deadline_elapsed {
                TextSelection::unavailable("error", "observation_deadline_elapsed")
            } else {
                read(&node.object)
            });
        }
    }
}

fn read_node(conn: &Connection, object: Object, parent: Option<Object>, depth: usize) -> R<Node> {
    let role_id: u32 = conn
        .call_method(
            Some(object.bus.as_str()),
            object.path.as_str(),
            Some(ACCESSIBLE),
            "GetRole",
            &(),
        )
        .map_err(|e| e.to_string())?
        .body()
        .deserialize()
        .map_err(|e| e.to_string())?;
    let role = string_call(conn, &object, ACCESSIBLE, "GetRoleName")?;
    let protected = role_id == 40 || role.to_lowercase().contains("password");
    let name = if protected {
        "[protected field]".into()
    } else {
        String::try_from(property(conn, &object, "Name")?).map_err(|e| e.to_string())?
    };
    // GTK icon-only toolbar buttons often keep Name empty and expose the
    // human-facing tooltip through Description. Protected fields stay redacted.
    let description = if protected {
        "[protected field]".into()
    } else {
        property(conn, &object, "Description")
            .ok()
            .and_then(|value| String::try_from(value).ok())
            .unwrap_or_default()
    };
    let interfaces: Vec<String> = conn
        .call_method(
            Some(object.bus.as_str()),
            object.path.as_str(),
            Some(ACCESSIBLE),
            "GetInterfaces",
            &(),
        )
        .map_err(|e| e.to_string())?
        .body()
        .deserialize()
        .map_err(|e| e.to_string())?;
    let states = node_states(conn, &object)?;
    let action_names = if !protected && interfaces.iter().any(|s| s == ACTION) {
        action_names(conn, &object)?
    } else {
        Vec::new()
    };
    let text_excerpt = if !protected && interfaces.iter().any(|s| s == TEXT) {
        Some(read_text(conn, &object, 512, false)?)
    } else {
        None
    };
    Ok(Node {
        object,
        parent,
        depth,
        name,
        description,
        role,
        role_id,
        interfaces,
        states,
        protected,
        action_names,
        text_excerpt,
        text_selection: None,
    })
}
fn is_window(node: &Node) -> bool {
    matches!(
        node.role.to_lowercase().as_str(),
        "frame" | "window" | "dialog"
    )
}
struct VisibleWalk {
    nodes: Vec<Node>,
    complete: bool,
    statistics: VisibilityTraversal,
    incomplete_reasons: IncompleteReasons,
}
/// Capture only the selected root and SHOWING paths below it. Hidden branches
/// are deliberately outside this scope, not a claim about the whole application.
/// A direct child contradicting its hidden parent's SHOWING state invalidates
/// the capture. Deeper omitted descendants are not enumerated or attested.
fn walk_visible_window(
    window: Object,
    application: Object,
    max_nodes: usize,
    max_depth: usize,
    mut read: impl FnMut(Object, Option<Object>, usize) -> R<Node>,
    mut get_children: impl FnMut(&Object) -> R<Vec<Object>>,
    mut get_states: impl FnMut(&Object) -> R<Vec<u32>>,
    mut expired: impl FnMut() -> bool,
) -> VisibleWalk {
    let mut stack = vec![(window.clone(), Some(application), 0usize)];
    let mut visited = HashSet::new();
    let mut result = VisibleWalk {
        nodes: Vec::new(),
        complete: true,
        incomplete_reasons: IncompleteReasons::default(),
        statistics: VisibilityTraversal {
            definition: VISIBLE_TRAVERSAL.into(),
            ..VisibilityTraversal::default()
        },
    };
    while let Some((obj, parent, depth)) = stack.pop() {
        if expired() {
            result.incomplete_reasons.traversal_deadline_cutoffs += 1;
            result.complete = false;
            break;
        }
        if !visited.insert(obj.clone()) {
            result.incomplete_reasons.repeated_object_references += 1;
            result.complete = false;
            continue;
        }
        let node = match read(obj.clone(), parent, depth) {
            Ok(node) if !node.states.is_empty() => node,
            _ => {
                result.incomplete_reasons.node_read_or_state_errors += 1;
                result.complete = false;
                result.statistics.visibility_probe_errors += 1;
                continue;
            }
        };
        let root = obj == window;
        let showing = state(&node.states, 25);
        if root {
            result.statistics.selected_root_showing = showing;
        }
        if !showing && !root {
            result.statistics.pruned_not_showing_branches += 1;
            if node.protected {
                // Protected descendants remain uninspected, exactly as before.
                result.statistics.pruned_protected_branches += 1;
                continue;
            }
            match get_children(&obj) {
                Ok(children) => {
                    for child in children {
                        if expired() {
                            result.incomplete_reasons.traversal_deadline_cutoffs += 1;
                            result.complete = false;
                            break;
                        }
                        result.statistics.immediate_child_visibility_probes += 1;
                        match get_states(&child) {
                            Ok(states) if !states.is_empty() => {
                                if state(&states, 25) {
                                    result.incomplete_reasons.showing_child_under_hidden_parent +=
                                        1;
                                    result.complete = false;
                                    result.statistics.showing_child_under_hidden_parent += 1;
                                }
                            }
                            _ => {
                                result.incomplete_reasons.child_visibility_probe_errors += 1;
                                result.complete = false;
                                result.statistics.visibility_probe_errors += 1;
                            }
                        }
                    }
                }
                Err(_) => {
                    result.incomplete_reasons.child_enumeration_errors += 1;
                    result.complete = false;
                    result.statistics.visibility_probe_errors += 1;
                }
            }
            continue;
        }
        if result.nodes.len() >= max_nodes {
            result.incomplete_reasons.max_nodes_cutoffs += 1;
            result.complete = false;
            break;
        }
        let protected = node.protected;
        result.nodes.push(node);
        if !showing {
            // Keep the requested root for diagnosis, but never issue mutation
            // handles when the selected window's bridge denies visibility.
            result.incomplete_reasons.selected_root_not_showing += 1;
            result.complete = false;
            continue;
        }
        if protected {
            result.statistics.pruned_protected_branches += 1;
            continue;
        }
        match get_children(&obj) {
            Ok(children) => {
                if depth >= max_depth {
                    if !children.is_empty() {
                        result.incomplete_reasons.max_depth_cutoffs += 1;
                        result.complete = false;
                    }
                    continue;
                }
                for child in children.into_iter().rev() {
                    stack.push((child, Some(obj.clone()), depth + 1));
                }
            }
            Err(_) => {
                result.incomplete_reasons.child_enumeration_errors += 1;
                result.complete = false;
            }
        }
    }
    result
}
fn observe(
    conn: &Connection,
    pid: u32,
    title: &str,
    single_window: bool,
    max_nodes: usize,
    max_depth: usize,
    include_text_selection: bool,
) -> R<Snapshot> {
    let started = Instant::now();
    let registry = Object {
        bus: "org.a11y.atspi.Registry".into(),
        path: "/org/a11y/atspi/accessible/root".into(),
    };
    let mut apps = Vec::new();
    for application in children(conn, &registry)? {
        if owner_pid(conn, &application.bus).ok() == Some(pid) {
            apps.push(application);
        }
    }
    if apps.len() != 1 {
        return Err(format!(
            "AT-SPI requires exactly one application bus owned by requested PID; found {}",
            apps.len()
        ));
    }
    let application = apps.remove(0);
    let mut windows = Vec::new();
    let mut discovery_complete = true;
    let mut discovery_reasons = IncompleteReasons::default();
    for obj in children(conn, &application)? {
        if started.elapsed() > OBSERVE_DEADLINE {
            discovery_reasons.window_discovery_deadline_cutoffs += 1;
            discovery_complete = false;
            break;
        }
        match read_node(conn, obj, Some(application.clone()), 0) {
            Ok(node) => {
                if is_window(&node) {
                    windows.push(node);
                }
            }
            Err(_) => {
                discovery_reasons.window_discovery_read_errors += 1;
                discovery_complete = false;
            }
        }
    }
    let matches: Vec<&Node> = windows.iter().filter(|node| node.name == title).collect();
    let window = if matches.len() == 1 {
        matches[0].object.clone()
    } else if matches.is_empty() && single_window && windows.len() == 1 {
        windows[0].object.clone()
    } else {
        return Err(format!("Exact AT-SPI window mapping is ambiguous (title matches {}, windows {}); no application-wide mutation allowed",matches.len(),windows.len()));
    };
    let walked = walk_visible_window(
        window.clone(),
        application.clone(),
        max_nodes,
        max_depth,
        |object, parent, depth| read_node(conn, object, parent, depth),
        |object| children(conn, object),
        |object| node_states(conn, object),
        || started.elapsed() > OBSERVE_DEADLINE,
    );
    let mut nodes = walked.nodes;
    if include_text_selection {
        add_selection_readback(&mut nodes, started.elapsed() > OBSERVE_DEADLINE, |object| {
            read_selection(conn, object)
        });
    }
    let complete = discovery_complete && walked.complete;
    let mut incomplete_reasons = walked.incomplete_reasons;
    incomplete_reasons.window_discovery_deadline_cutoffs =
        discovery_reasons.window_discovery_deadline_cutoffs;
    incomplete_reasons.window_discovery_read_errors =
        discovery_reasons.window_discovery_read_errors;
    let traversal_diagnostics = Some(TraversalDiagnostics {
        revision: 1,
        effective_max_nodes: max_nodes,
        effective_max_depth: max_depth,
        effective_deadline_ms: OBSERVE_DEADLINE.as_millis() as u64,
        incomplete_reasons,
    });
    // A modal can be a sibling application top-level, outside the selected
    // window subtree. Include these too so background AX cannot bypass it.
    let mut visible_modals: Vec<Object> = windows
        .iter()
        .chain(nodes.iter())
        .filter(|n| {
            state(&n.states, 25) && (state(&n.states, 16) || n.role.to_lowercase() == "dialog")
        })
        .map(|n| n.object.clone())
        .collect();
    visible_modals.sort_by(|a, b| (&a.bus, &a.path).cmp(&(&b.bus, &b.path)));
    visible_modals.dedup();
    Ok(Snapshot{pid,requested_title:title.into(),application,window,nodes,complete,visible_modals,semantic_scope:"exact selected AT-SPI window root plus SHOWING descendants under SHOWING ancestors; non-SHOWING and protected branches excluded. Completeness covers only this defined visible scope, not omitted descendants, hidden tabs or the whole application. Correct bridge SHOWING ancestry is required; immediate contradictions fail closed. Sibling top-level showing modal guards remain. Targets use unique-owner D-Bus object references, never integer indices".into(),sole_window_fallback_allowed:single_window,bus_guid:conn.server_guid().into(),max_nodes,max_depth,visibility_traversal:walked.statistics,traversal_diagnostics})
}
fn revalidate(
    conn: &Connection,
    snapshot: &Snapshot,
    object: &Object,
    target_edit_text_precondition: bool,
) -> R<Node> {
    if snapshot.visibility_traversal.definition != VISIBLE_TRAVERSAL {
        return Err(
            "Semantic traversal definition changed; obtain a fresh visible-scope snapshot".into(),
        );
    }
    if !snapshot.complete {
        return Err("Observed semantic traversal was incomplete; mutation refused because modal coverage is uncertain".into());
    }
    if conn.server_guid() != snapshot.bus_guid {
        return Err("AT-SPI bus generation changed; old object handles invalidated".into());
    }
    if !object.bus.starts_with(':') || !snapshot.application.bus.starts_with(':') {
        return Err("Semantic target requires immutable unique D-Bus owner names".into());
    }
    owner_pid(conn, &object.bus)?;
    if owner_pid(conn, &snapshot.application.bus)? != snapshot.pid {
        return Err("Application D-Bus owner PID changed".into());
    }
    let prior = snapshot
        .nodes
        .iter()
        .find(|n| &n.object == object)
        .ok_or("Handle is not in the stored window subtree")?;
    if prior.protected {
        return Err("Protected fields are not editable by this adapter".into());
    }
    let mut current = read_node(conn, object.clone(), prior.parent.clone(), prior.depth)?;
    if current.protected {
        return Err("Current target is a protected field; mutation refused".into());
    }
    if (
        current.role_id,
        &current.name,
        &current.description,
        &current.interfaces,
    ) != (
        prior.role_id,
        &prior.name,
        &prior.description,
        &prior.interfaces,
    ) {
        return Err(
            "Semantic object role/name/description/interfaces changed; observe again".into(),
        );
    }
    if state(&current.states, 3)
        || state(&current.states, 6)
        || !state(&current.states, 8)
        || !state(&current.states, 24)
        || !state(&current.states, 25)
    {
        return Err("Semantic target is busy/defunct or not enabled, sensitive and showing".into());
    }
    let mut chain = prior;
    let mut ancestors = HashSet::from([prior.object.clone()]);
    while let Some(parent) = &chain.parent {
        if parent == &snapshot.application {
            break;
        }
        if !children(conn, parent)?.contains(&chain.object) {
            return Err("Object is no longer a child of its observed window ancestry".into());
        }
        chain = snapshot
            .nodes
            .iter()
            .find(|n| &n.object == parent)
            .ok_or("Observed parent identity unavailable")?;
        ancestors.insert(chain.object.clone());
        let fresh_parent = read_node(conn, parent.clone(), chain.parent.clone(), chain.depth)?;
        if (
            fresh_parent.role_id,
            &fresh_parent.name,
            &fresh_parent.description,
        ) != (chain.role_id, &chain.name, &chain.description)
        {
            return Err("Semantic ancestor context changed; observe again".into());
        }
    }
    if snapshot
        .visible_modals
        .iter()
        .any(|modal| !ancestors.contains(modal))
    {
        return Err("Target lies outside a showing modal's subtree; direct accessibility cannot bypass modality".into());
    }
    // Rewalk only for modal and target-window identity; mutation still uses the
    // original object path, never a newly allocated integer index.
    let fresh = observe(
        conn,
        snapshot.pid,
        &snapshot.requested_title,
        snapshot.sole_window_fallback_allowed,
        snapshot.max_nodes.clamp(1, 1000),
        snapshot.max_depth.clamp(1, 40),
        false,
    )?;
    if !fresh.complete {
        return Err("Fresh semantic traversal was incomplete; mutation refused because modal coverage is uncertain".into());
    }
    if fresh.visibility_traversal.definition != snapshot.visibility_traversal.definition {
        return Err("Fresh semantic traversal definition differs; mutation refused".into());
    }
    if fresh.window != snapshot.window || fresh.visible_modals != snapshot.visible_modals {
        return Err("Target window or visible modal set changed; observe again".into());
    }
    let context = |nodes: &[Node]| {
        let mut fields:Vec<Value>=nodes.iter().filter(|n|state(&n.states,25)).map(|n|json!({"object":n.object,"role_id":n.role_id,"name":n.name,"description":n.description,"interfaces":n.interfaces,"visible_text":if target_edit_text_precondition&&n.object==*object{None}else{n.text_excerpt.as_ref()}})).collect();
        fields.sort_by_key(|v| v["object"].to_string());
        fields
    };
    if context(&fresh.nodes) != context(&snapshot.nodes) {
        return Err(
            "Visible semantic window context changed; no mutation sent. Observe again.".into(),
        );
    }
    current.parent = prior.parent.clone();
    Ok(current)
}
// Production focus boundary. Validation is repeated on the same immutable
// object after GrabFocus; its boolean acceptance is never the FOCUSED proof.
fn focus_with_revalidation(
    mut validate: impl FnMut() -> R<Node>,
    mut grab: impl FnMut() -> R<bool>,
) -> R<Value> {
    let capable = |node: &Node| {
        !node.protected
            && node
                .interfaces
                .iter()
                .any(|s| s == "org.a11y.atspi.Component")
            && state(&node.states, 11)
            && state(&node.states, 8)
            && state(&node.states, 24)
            && state(&node.states, 25)
            && !state(&node.states, 3)
            && !state(&node.states, 6)
    };
    let before = validate()?;
    if !capable(&before) {
        return Err("Target lacks enabled/showing/focusable AT-SPI Component capability; no focus or fallback sent".into());
    }
    let accepted = grab()?;
    if !accepted {
        return Ok(
            json!({"status":"failed","route":"direct_atspi_object","accepted":false,"focus_verified":false,"focused":null,"keyboard_input_dispatched":false,"ui_result_verified":false}),
        );
    }
    let after = match validate() {
        Ok(node) => node,
        Err(error) => {
            return Ok(
                json!({"status":"uncertain","route":"direct_atspi_object","accepted":true,"focus_verified":false,"focused":null,"keyboard_input_dispatched":false,"ui_result_verified":false,"readback_error":error}),
            )
        }
    };
    let focused = state(&after.states, 12);
    let verified = capable(&after) && focused;
    Ok(
        json!({"status":if verified{"focus_verified"}else{"uncertain"},"route":"direct_atspi_object","accepted":true,"focus_verified":verified,"focused":focused,"keyboard_input_dispatched":false,"ui_result_verified":false}),
    )
}

pub fn run(request: Value) -> R<Value> {
    let started = Instant::now();
    let conn = connection()?;
    let connect_ms = started.elapsed().as_secs_f64() * 1000.0;
    match request["operation"].as_str().unwrap_or("") {
        "observe" => {
            let pid = request["pid"]
                .as_u64()
                .filter(|n| *n <= u32::MAX as u64)
                .ok_or("PID required")? as u32;
            let title = request["title"].as_str().ok_or("Title required")?;
            let snapshot = observe(
                &conn,
                pid,
                title,
                request["single_window"].as_bool() == Some(true),
                request["max_nodes"].as_u64().unwrap_or(1000).clamp(1, 1000) as usize,
                request["max_depth"].as_u64().unwrap_or(40).clamp(1, 40) as usize,
                request["include_text_selection"].as_bool() == Some(true),
            )?;
            Ok(
                json!({"status":"observed","snapshot":snapshot,"latency_ms":started.elapsed().as_secs_f64()*1000.0}),
            )
        }
        "focus" => {
            let snapshot: Snapshot =
                serde_json::from_value(request["snapshot"].clone()).map_err(|e| e.to_string())?;
            let object: Object =
                serde_json::from_value(request["object"].clone()).map_err(|e| e.to_string())?;
            let mut result = focus_with_revalidation(
                || {
                    let mut node = revalidate(&conn, &snapshot, &object, false)?;
                    // Query state after the complete identity/ancestry/modal
                    // readback, not from the earlier validation's cached node.
                    node.states = node_states(&conn, &object)?;
                    Ok(node)
                },
                || {
                    conn.call_method(
                        Some(object.bus.as_str()),
                        object.path.as_str(),
                        Some("org.a11y.atspi.Component"),
                        "GrabFocus",
                        &(),
                    )
                    .map_err(|e| e.to_string())?
                    .body()
                    .deserialize()
                    .map_err(|e| e.to_string())
                },
            )?;
            result["latency_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
            Ok(result)
        }
        "set_value" | "click" => {
            let snapshot: Snapshot =
                serde_json::from_value(request["snapshot"].clone()).map_err(|e| e.to_string())?;
            let object: Object =
                serde_json::from_value(request["object"].clone()).map_err(|e| e.to_string())?;
            let validation_started = Instant::now();
            let node = revalidate(
                &conn,
                &snapshot,
                &object,
                request["operation"] == "set_value",
            )?;
            let revalidation_ms = validation_started.elapsed().as_secs_f64() * 1000.0;
            if request["operation"] == "set_value" {
                if !node
                    .interfaces
                    .iter()
                    .any(|s| s == "org.a11y.atspi.EditableText")
                    || !state(&node.states, 7)
                {
                    return Err("Target has no editable AT-SPI text capability".into());
                }
                let text = request["text"].as_str().ok_or("Text required")?;
                if text.len() > 65536 {
                    return Err("Text exceeds 64KiB".into());
                }
                let prior = snapshot
                    .nodes
                    .iter()
                    .find(|n| n.object == object)
                    .ok_or("Target absent from stored snapshot")?;
                let expected = request["expected_text"]
                    .as_str()
                    .or(prior.text_excerpt.as_deref())
                    .ok_or("Expected complete editable text is required")?;
                let current_text = read_full_text(&conn, &object)?;
                if current_text != expected {
                    return Err("Current editable buffer differs from expected complete text; no mutation sent. Observe again or pass an explicit expected_text.".into());
                }
                let accepted: bool = conn
                    .call_method(
                        Some(object.bus.as_str()),
                        object.path.as_str(),
                        Some("org.a11y.atspi.EditableText"),
                        "SetTextContents",
                        &(text,),
                    )
                    .map_err(|e| e.to_string())?
                    .body()
                    .deserialize()
                    .map_err(|e| e.to_string())?;
                if !accepted {
                    return Err("AT-SPI SetTextContents refused; no insertion or keyboard fallback attempted".into());
                }
                let fresh_text = match read_full_text(&conn, &object) {
                    Ok(text) => text,
                    Err(error) => {
                        return Ok(
                            json!({"status":"uncertain","route":"direct_atspi_object","accepted":true,"exact_buffer_bytes_match":false,"saved_artifact_verified":false,"readback_error":error,"connect_ms":connect_ms,"revalidation_ms":revalidation_ms,"latency_ms":started.elapsed().as_secs_f64()*1000.0}),
                        )
                    }
                };
                Ok(
                    json!({"status":if fresh_text==text{"buffer_verified"}else{"uncertain"},"route":"direct_atspi_object","accepted":true,"exact_buffer_bytes_match":fresh_text.as_bytes()==text.as_bytes(),"saved_artifact_verified":false,"connect_ms":connect_ms,"revalidation_ms":revalidation_ms,"latency_ms":started.elapsed().as_secs_f64()*1000.0}),
                )
            } else {
                if !node.interfaces.iter().any(|s| s == "org.a11y.atspi.Action") {
                    return Err(
                        "Target has no AT-SPI Action capability; no pointer fallback".into(),
                    );
                }
                let action = request["action_name"].as_str().unwrap_or("click");
                let actions = action_names(&conn, &object)?;
                let index = named_action_index(&actions, action)?;
                let accepted: bool = conn
                    .call_method(
                        Some(object.bus.as_str()),
                        object.path.as_str(),
                        Some("org.a11y.atspi.Action"),
                        "DoAction",
                        &(index,),
                    )
                    .map_err(|e| e.to_string())?
                    .body()
                    .deserialize()
                    .map_err(|e| e.to_string())?;
                Ok(
                    json!({"status":if accepted{"dispatched"}else{"failed"},"route":"direct_atspi_object","accepted":accepted,"ui_result_verified":false,"connect_ms":connect_ms,"revalidation_ms":revalidation_ms,"latency_ms":started.elapsed().as_secs_f64()*1000.0}),
                )
            }
        }
        _ => Err("Supported operations: observe, set_value, click, focus".into()),
    }
}

#[cfg(test)]
mod visible_scope_tests {
    use super::*;
    use std::{cell::RefCell, collections::HashMap};

    fn object(id: &str) -> Object {
        Object {
            bus: ":1.123".into(),
            path: format!("/test/{id}"),
        }
    }
    fn node(id: &str, showing: bool, protected: bool) -> Node {
        Node {
            object: object(id),
            parent: None,
            depth: 0,
            name: if protected {
                "[protected field]".into()
            } else {
                id.into()
            },
            description: String::new(),
            role: "panel".into(),
            role_id: 39,
            interfaces: Vec::new(),
            states: vec![if showing { 1 << 25 } else { 0 }],
            protected,
            action_names: Vec::new(),
            text_excerpt: None,
            text_selection: None,
        }
    }
    fn walk(
        nodes: Vec<Node>,
        edges: &[(&str, &[&str])],
        max_nodes: usize,
        max_depth: usize,
        state_error: Option<&str>,
        expired: bool,
    ) -> (VisibleWalk, Vec<String>, Vec<String>) {
        let by: HashMap<Object, Node> = nodes.into_iter().map(|n| (n.object.clone(), n)).collect();
        let children: HashMap<Object, Vec<Object>> = edges
            .iter()
            .map(|(p, c)| (object(p), c.iter().map(|id| object(id)).collect()))
            .collect();
        let reads = RefCell::new(Vec::new());
        let children_calls = RefCell::new(Vec::new());
        let result = walk_visible_window(
            object("root"),
            object("application"),
            max_nodes,
            max_depth,
            |obj, parent, depth| {
                reads.borrow_mut().push(obj.path.clone());
                let mut n = by.get(&obj).cloned().ok_or("node absent")?;
                n.parent = parent;
                n.depth = depth;
                Ok(n)
            },
            |obj| {
                children_calls.borrow_mut().push(obj.path.clone());
                Ok(children.get(obj).cloned().unwrap_or_default())
            },
            |obj| {
                if state_error.is_some_and(|id| object(id) == *obj) {
                    return Err("bridge state query failed".into());
                }
                Ok(by.get(obj).ok_or("state absent")?.states.clone())
            },
            || expired,
        );
        (result, reads.into_inner(), children_calls.into_inner())
    }
    #[test]
    fn hidden_tab_descendants_are_not_traversed_or_counted_as_visible_nodes() {
        let (r, reads, _) = walk(
            vec![
                node("root", true, false),
                node("hidden_tab", false, false),
                node("hidden_document", false, false),
                node("visible", true, false),
            ],
            &[
                ("root", &["hidden_tab", "visible"]),
                ("hidden_tab", &["hidden_document"]),
                ("hidden_document", &["omitted_descendant"]),
            ],
            2,
            40,
            None,
            false,
        );
        assert!(r.complete);
        assert_eq!(
            r.nodes.iter().map(|n| &n.name).collect::<Vec<_>>(),
            vec!["root", "visible"]
        );
        assert!(!reads.iter().any(|p| p.ends_with("hidden_document")));
        assert_eq!(r.statistics.pruned_not_showing_branches, 1);
        assert_eq!(r.statistics.immediate_child_visibility_probes, 1);
        assert_eq!(r.statistics.omitted_descendant_count, None);
    }
    #[test]
    fn contradictory_showing_child_under_hidden_parent_fails_closed() {
        let (r, _, _) = walk(
            vec![
                node("root", true, false),
                node("hidden", false, false),
                node("showing_dialog", true, false),
            ],
            &[("root", &["hidden"]), ("hidden", &["showing_dialog"])],
            100,
            40,
            None,
            false,
        );
        assert!(!r.complete);
        assert_eq!(r.statistics.showing_child_under_hidden_parent, 1);
        assert_eq!(r.incomplete_reasons.showing_child_under_hidden_parent, 1);
        assert_eq!(r.nodes.len(), 1);
    }
    #[test]
    fn protected_branches_keep_redacted_root_but_never_open_descendants() {
        for showing in [true, false] {
            let (r, reads, children) = walk(
                vec![
                    node("root", true, false),
                    node("secret", showing, true),
                    node("secret_child", true, false),
                ],
                &[("root", &["secret"]), ("secret", &["secret_child"])],
                100,
                40,
                None,
                false,
            );
            assert!(r.complete);
            assert!(!reads.iter().any(|p| p.ends_with("secret_child")));
            assert!(!children.iter().any(|p| p.ends_with("secret")));
            assert_eq!(r.statistics.pruned_protected_branches, 1);
            assert!(r
                .nodes
                .iter()
                .filter(|n| n.protected)
                .all(|n| n.name == "[protected field]"));
        }
    }
    #[test]
    fn selected_root_is_retained_but_false_showing_prevents_mutation_handles() {
        let (r, reads, _) = walk(
            vec![node("root", false, false), node("visible", true, false)],
            &[("root", &["visible"])],
            100,
            40,
            None,
            false,
        );
        assert!(!r.complete);
        assert_eq!(r.nodes.len(), 1);
        assert!(!r.statistics.selected_root_showing);
        assert_eq!(r.incomplete_reasons.selected_root_not_showing, 1);
        assert_eq!(reads, vec!["/test/root"]);
    }
    #[test]
    fn visibility_probe_failure_is_not_interpreted_as_hidden_or_complete() {
        let (r, _, _) = walk(
            vec![
                node("root", true, false),
                node("hidden", false, false),
                node("child", false, false),
            ],
            &[("root", &["hidden"]), ("hidden", &["child"])],
            100,
            40,
            Some("child"),
            false,
        );
        assert!(!r.complete);
        assert_eq!(r.statistics.visibility_probe_errors, 1);
        assert_eq!(r.incomplete_reasons.child_visibility_probe_errors, 1);
    }
    #[test]
    fn empty_state_words_are_unknown_visibility_not_a_pruning_success() {
        let mut unknown = node("unknown", false, false);
        unknown.states.clear();
        let (r, _, _) = walk(
            vec![node("root", true, false), unknown],
            &[("root", &["unknown"])],
            100,
            40,
            None,
            false,
        );
        assert!(!r.complete);
        assert_eq!(r.statistics.visibility_probe_errors, 1);
        assert_eq!(r.incomplete_reasons.node_read_or_state_errors, 1);
    }
    #[test]
    fn visible_node_limit_depth_limit_and_deadline_are_incomplete() {
        for (budget, depth, expired) in [(1, 40, false), (100, 0, false), (100, 40, true)] {
            let (r, _, _) = walk(
                vec![node("root", true, false), node("visible", true, false)],
                &[("root", &["visible"])],
                budget,
                depth,
                None,
                expired,
            );
            assert!(!r.complete);
            assert_eq!(
                r.incomplete_reasons.max_nodes_cutoffs,
                usize::from(budget == 1)
            );
            assert_eq!(
                r.incomplete_reasons.max_depth_cutoffs,
                usize::from(depth == 0)
            );
            assert_eq!(
                r.incomplete_reasons.traversal_deadline_cutoffs,
                usize::from(expired)
            );
            assert_eq!(r.incomplete_reasons.node_read_or_state_errors, 0);
        }
    }
    #[test]
    fn duplicate_object_ancestry_cannot_claim_a_complete_tree() {
        let (r, _, _) = walk(
            vec![node("root", true, false), node("visible", true, false)],
            &[("root", &["visible", "visible"])],
            100,
            40,
            None,
            false,
        );
        assert!(!r.complete);
        assert_eq!(r.incomplete_reasons.repeated_object_references, 1);
    }
    #[test]
    fn diagnostics_report_node_and_child_errors_without_exporting_private_error_text() {
        let (missing, _, _) = walk(
            vec![node("root", true, false)],
            &[("root", &["missing"])],
            100,
            40,
            None,
            false,
        );
        assert!(!missing.complete);
        assert_eq!(missing.incomplete_reasons.node_read_or_state_errors, 1);
        for hidden in [false, true] {
            let result = walk_visible_window(
                object("root"),
                object("application"),
                100,
                40,
                |obj, parent, depth| {
                    let mut n = node(
                        if obj == object("root") {
                            "root"
                        } else {
                            "hidden"
                        },
                        obj == object("root"),
                        false,
                    );
                    n.parent = parent;
                    n.depth = depth;
                    Ok(n)
                },
                |obj| {
                    if hidden && *obj == object("root") {
                        Ok(vec![object("hidden")])
                    } else {
                        Err("private D-Bus path and bridge exception must not be exported".into())
                    }
                },
                |_| panic!("No children returned, no visibility probes needed"),
                || false,
            );
            assert!(!result.complete);
            assert_eq!(result.incomplete_reasons.child_enumeration_errors, 1);
            assert_eq!(result.incomplete_reasons.node_read_or_state_errors, 0);
            let diagnostics = serde_json::to_value(&result.incomplete_reasons).unwrap();
            assert!(diagnostics.as_object().unwrap().values().all(Value::is_u64));
            assert!(!diagnostics.to_string().contains("private"));
        }
    }

    #[test]
    fn deadline_inside_hidden_child_probe_preserves_scope_and_reports_actual_cutoff() {
        let calls = std::cell::Cell::new(0);
        let result = walk_visible_window(
            object("root"),
            object("application"),
            100,
            40,
            |obj, parent, depth| {
                let mut n = node(
                    if obj == object("root") {
                        "root"
                    } else {
                        "hidden"
                    },
                    obj == object("root"),
                    false,
                );
                n.parent = parent;
                n.depth = depth;
                Ok(n)
            },
            |obj| {
                Ok(vec![object(if *obj == object("root") {
                    "hidden"
                } else {
                    "unread_child"
                })])
            },
            |_| panic!("Deadline cutoff must precede the child state probe"),
            || {
                let n = calls.get() + 1;
                calls.set(n);
                n >= 3
            },
        );
        assert!(!result.complete);
        assert_eq!(result.incomplete_reasons.traversal_deadline_cutoffs, 1);
        assert_eq!(result.statistics.pruned_not_showing_branches, 1);
        assert_eq!(result.statistics.immediate_child_visibility_probes, 0);
        assert_eq!(result.nodes.len(), 1);
        assert_eq!(result.nodes[0].name, "root");
    }

    #[test]
    fn legacy_snapshot_diagnostics_are_unknown_and_new_typed_evidence_roundtrips() {
        let old = json!({"pid":42,"requested_title":"test","application":{"bus":":1.test","path":"/test/app"},"window":{"bus":":1.test","path":"/test/window"},"nodes":[],"complete":false,"visible_modals":[],"semantic_scope":"test","sole_window_fallback_allowed":false,"bus_guid":"no-connection"});
        let mut snapshot: Snapshot = serde_json::from_value(old).unwrap();
        assert!(snapshot.traversal_diagnostics.is_none());
        assert_eq!(snapshot.max_nodes, 1000);
        assert_eq!(snapshot.max_depth, 40);
        snapshot.traversal_diagnostics = Some(TraversalDiagnostics {
            revision: 1,
            effective_max_nodes: 200,
            effective_max_depth: 12,
            effective_deadline_ms: OBSERVE_DEADLINE.as_millis() as u64,
            incomplete_reasons: IncompleteReasons {
                max_depth_cutoffs: 1,
                ..IncompleteReasons::default()
            },
        });
        let serialized = serde_json::to_value(&snapshot).unwrap();
        let restored: Snapshot = serde_json::from_value(serialized.clone()).unwrap();
        assert!(!restored.complete);
        assert_eq!(
            serialized["traversal_diagnostics"]["effective_deadline_ms"],
            3000
        );
        assert_eq!(
            serialized["traversal_diagnostics"]["incomplete_reasons"]["max_depth_cutoffs"],
            1
        );
        assert_eq!(serde_json::to_value(restored).unwrap(), serialized);
    }

    #[test]
    fn fresh_traversal_removes_a_target_that_becomes_hidden() {
        let (before, _, _) = walk(
            vec![node("root", true, false), node("target", true, false)],
            &[("root", &["target"])],
            100,
            40,
            None,
            false,
        );
        let (after, _, _) = walk(
            vec![node("root", true, false), node("target", false, false)],
            &[("root", &["target"])],
            100,
            40,
            None,
            false,
        );
        assert!(before.complete && after.complete);
        assert!(before.nodes.iter().any(|n| n.object == object("target")));
        assert!(!after.nodes.iter().any(|n| n.object == object("target")));
        assert_eq!(before.statistics.definition, after.statistics.definition);
        assert_eq!(before.statistics.definition, VISIBLE_TRAVERSAL);
    }
}

#[cfg(test)]
mod bounded_bridge_read_tests {
    use super::*;
    #[test]
    fn individual_machine_action_names_are_authoritative() {
        let names =
            read_action_names(|| Ok(2), |i| Ok(["activate", "press"][i as usize].into())).unwrap();
        assert_eq!(names, vec!["activate", "press"]);
        assert_eq!(named_action_index(&names, "press").unwrap(), 1);
        assert!(named_action_index(&names, "click").is_err());
    }
    #[test]
    fn missing_empty_oversized_or_changing_actions_fail_closed() {
        assert!(read_action_names(|| Ok(65), |_| panic!("not called")).is_err());
        assert!(read_action_names(|| Ok(-1), |_| panic!("not called")).is_err());
        assert!(read_action_names(|| Ok(1), |_| Ok(String::new())).is_err());
        assert!(read_action_names(|| Ok(1), |_| Err("name unavailable".into())).is_err());
        let mut counts = [1, 2].into_iter();
        assert!(read_action_names(|| Ok(counts.next().unwrap()), |_| Ok("press".into())).is_err());
        assert!(named_action_index(&["press".into(), "press".into()], "press").is_err());
    }
    #[test]
    fn short_gecko_ranges_never_exceed_character_count() {
        let text = read_text_bounded(
            || Ok(3),
            |end| {
                assert_eq!(end, 3);
                Ok("ä🦦ß".into())
            },
            512,
            false,
        )
        .unwrap();
        assert_eq!(text, "ä🦦ß");
        assert_eq!(
            read_text_bounded(
                || Ok(0),
                |end| {
                    assert_eq!(end, 0);
                    Ok(String::new())
                },
                512,
                false
            )
            .unwrap(),
            ""
        );
    }
    #[test]
    fn excerpt_and_complete_read_have_distinct_bounds() {
        assert_eq!(
            read_text_bounded(
                || Ok(600),
                |end| {
                    assert_eq!(end, 512);
                    Ok("a".repeat(512))
                },
                512,
                false
            )
            .unwrap()
            .len(),
            512
        );
        assert!(read_text_bounded(
            || Ok(65537),
            |_| panic!("no unbounded request"),
            65536,
            true
        )
        .is_err());
        assert!(read_text_bounded(|| Ok(16385), |_| Ok("🦦".repeat(16385)), 65536, true).is_err());
    }
    #[test]
    fn empty_truncated_utf16_or_racing_text_is_not_complete() {
        assert!(read_text_bounded(|| Ok(3), |_| Ok(String::new()), 512, false).is_err());
        assert!(read_text_bounded(|| Ok(2), |_| Ok("🦦".into()), 512, false).is_err());
        let mut counts = [3, 4].into_iter();
        assert!(read_text_bounded(
            || Ok(counts.next().unwrap()),
            |_| Ok("abc".into()),
            512,
            false
        )
        .is_err());
    }
}

#[cfg(test)]
mod focus_tests {
    use super::*;
    use std::cell::Cell;

    fn target(focused: bool) -> Node {
        Node {
            object: Object {
                bus: ":1.123".into(),
                path: "/test/text".into(),
            },
            parent: None,
            depth: 1,
            name: "owned text".into(),
            description: String::new(),
            role: "text".into(),
            role_id: 60,
            interfaces: vec!["org.a11y.atspi.Component".into()],
            states: vec![
                (1 << 8) | (1 << 11) | (1 << 24) | (1 << 25) | if focused { 1 << 12 } else { 0 },
            ],
            protected: false,
            action_names: vec![],
            text_excerpt: Some("owned source".into()),
            text_selection: None,
        }
    }
    #[test]
    fn focus_refuses_missing_capability_invalid_state_and_prevalidation_before_dispatch() {
        for reason in [
            "component",
            "focusable",
            "showing",
            "enabled",
            "sensitive",
            "busy",
            "defunct",
            "protected",
            "identity",
            "modal",
            "ancestor",
            "owner",
        ] {
            let calls = Cell::new(0);
            let mut node = target(false);
            match reason {
                "component" => node.interfaces.clear(),
                "focusable" => node.states[0] &= !(1 << 11),
                "showing" => node.states[0] &= !(1 << 25),
                "enabled" => node.states[0] &= !(1 << 8),
                "sensitive" => node.states[0] &= !(1 << 24),
                "busy" => node.states[0] |= 1 << 3,
                "defunct" => node.states[0] |= 1 << 6,
                "protected" => node.protected = true,
                _ => {}
            }
            let result = focus_with_revalidation(
                || {
                    if matches!(reason, "identity" | "modal" | "ancestor" | "owner") {
                        Err(format!("existing revalidate {reason} guard"))
                    } else {
                        Ok(node.clone())
                    }
                },
                || {
                    calls.set(calls.get() + 1);
                    Ok(true)
                },
            );
            assert!(result.is_err(), "{reason}");
            assert_eq!(calls.get(), 0, "{reason}: no GrabFocus or fallback");
        }
    }
    #[test]
    fn focus_acceptance_requires_fresh_same_object_state_and_postvalidation() {
        for reason in [
            "verified",
            "accepted_without_focus",
            "changed_context",
            "no_longer_focusable",
            "refused",
        ] {
            let reads = Cell::new(0);
            let calls = Cell::new(0);
            let result = focus_with_revalidation(
                || {
                    reads.set(reads.get() + 1);
                    if reads.get() == 1 {
                        return Ok(target(false));
                    }
                    if reason == "changed_context" {
                        return Err("fresh exact object/window/modal validation failed".into());
                    }
                    let mut node = target(reason == "verified" || reason == "no_longer_focusable");
                    if reason == "no_longer_focusable" {
                        node.states[0] &= !(1 << 11);
                    }
                    Ok(node)
                },
                || {
                    calls.set(calls.get() + 1);
                    Ok(reason != "refused")
                },
            )
            .unwrap();
            assert_eq!(calls.get(), 1, "one dispatch, no repeat for {reason}");
            assert_eq!(reads.get(), if reason == "refused" { 1 } else { 2 });
            assert_eq!(result["focus_verified"], reason == "verified");
            assert_eq!(result["accepted"], reason != "refused");
            assert_eq!(result["keyboard_input_dispatched"], false);
            assert_eq!(result["ui_result_verified"], false);
            assert_eq!(
                result["status"],
                if reason == "verified" {
                    "focus_verified"
                } else if reason == "refused" {
                    "failed"
                } else {
                    "uncertain"
                }
            );
            if reason == "refused" {
                assert_eq!(
                    result["focused"],
                    Value::Null,
                    "refusal does not prove focus loss"
                );
            }
            if reason == "changed_context" {
                assert!(result["readback_error"]
                    .as_str()
                    .unwrap()
                    .contains("validation failed"));
            }
        }
    }

    #[test]
    fn selection_offsets_use_unicode_characters_not_utf8_or_utf16_units() {
        let text = "äß🙂e\u{301}\n";
        let characters = text.chars().count() as i32;
        assert_ne!(characters as usize, text.len());
        assert_ne!(characters as usize, text.encode_utf16().count());
        let value = read_selection_bounded(|| Ok(characters), || Ok(1), |_| Ok((0, characters)));
        assert_eq!(value.status, "available");
        assert_eq!(value.full_text_selected, Some(true));
        assert_eq!(
            value.ranges,
            vec![SelectionRange {
                start: 0,
                end: characters
            }]
        );
        assert_eq!(value.offset_unit, "unicode_codepoints");
        assert!(value.end_exclusive && value.stable_readback);
        assert!(!value.text_identity_verified);
    }
    #[test]
    fn selection_absent_caret_partial_and_empty_do_not_prove_full_selection() {
        for (characters, selections, range) in [
            (5, 0, (0, 0)),
            (5, 1, (2, 2)),
            (5, 1, (1, 5)),
            (0, 1, (0, 0)),
        ] {
            let calls = std::cell::Cell::new(0);
            let value = read_selection_bounded(
                || Ok(characters),
                || Ok(selections),
                |_| {
                    calls.set(calls.get() + 1);
                    Ok(range)
                },
            );
            assert_eq!(value.status, "available");
            assert_eq!(value.full_text_selected, Some(false));
            assert_eq!(calls.get(), if selections == 0 { 0 } else { 2 });
        }
    }
    #[test]
    fn selection_bad_counts_and_ranges_and_multiple_ranges_cannot_supply_proof() {
        for (characters, selections, range) in [
            (-1, 1, (0, 1)),
            (5, -1, (0, 5)),
            (5, 1, (-1, 5)),
            (5, 1, (3, 2)),
            (5, 1, (0, 6)),
            (5, 2, (0, 5)),
        ] {
            let calls = std::cell::Cell::new(0);
            let value = read_selection_bounded(
                || Ok(characters),
                || Ok(selections),
                |_| {
                    calls.set(calls.get() + 1);
                    Ok(range)
                },
            );
            assert_eq!(value.full_text_selected, None);
            assert!(!value.stable_readback);
            assert!(value.ranges.is_empty());
            if selections > 1 {
                assert_eq!(calls.get(), 0);
                assert_eq!(value.status, "unsupported");
            } else {
                assert_eq!(value.status, "error");
            }
        }
    }
    #[test]
    fn selection_dbus_refusal_and_error_are_not_zero_selections() {
        let unsupported = read_selection_bounded(
            || Ok(5),
            || Err(SelectionReadError::Unsupported),
            |_| panic!("unsupported bridge range must not be queried"),
        );
        assert_eq!(unsupported.status, "unsupported");
        assert_eq!(unsupported.full_text_selected, None);
        let failed =
            read_selection_bounded(|| Ok(5), || Ok(1), |_| Err(SelectionReadError::Failed));
        assert_eq!(failed.status, "error");
        assert_eq!(failed.full_text_selected, None);
        assert_eq!(
            failed.reason.as_deref(),
            Some("selection_range_unavailable")
        );
    }
    #[test]
    fn selection_same_count_range_change_and_count_change_are_detected() {
        for changed in ["characters", "count", "range"] {
            let c = std::cell::Cell::new(0);
            let n = std::cell::Cell::new(0);
            let r = std::cell::Cell::new(0);
            let value = read_selection_bounded(
                || {
                    c.set(c.get() + 1);
                    Ok(if changed == "characters" && c.get() == 2 {
                        6
                    } else {
                        5
                    })
                },
                || {
                    n.set(n.get() + 1);
                    Ok(if changed == "count" && n.get() == 2 {
                        0
                    } else {
                        1
                    })
                },
                |_| {
                    r.set(r.get() + 1);
                    Ok(if changed == "range" && r.get() == 2 {
                        (1, 5)
                    } else {
                        (0, 5)
                    })
                },
            );
            assert_eq!(value.status, "error");
            assert_eq!(value.full_text_selected, None);
            assert_eq!(
                value.reason.as_deref(),
                Some("selection_changed_during_readback")
            );
        }
    }
    #[test]
    fn selection_requires_live_text_focus_before_and_after_readback() {
        let reads = std::cell::Cell::new(0);
        let rejected = selection_with_focus_readback(
            || Ok(false),
            || {
                reads.set(reads.get() + 1);
                panic!("unfocused metadata must not be read")
            },
        );
        assert_eq!(reads.get(), 0);
        assert_eq!(rejected.full_text_selected, None);
        for mode in ["lost", "error", "retained"] {
            let states = std::cell::Cell::new(0);
            let value = selection_with_focus_readback(
                || {
                    states.set(states.get() + 1);
                    if states.get() == 1 || mode == "retained" {
                        Ok(true)
                    } else if mode == "error" {
                        Err("bridge unknown".into())
                    } else {
                        Ok(false)
                    }
                },
                || read_selection_bounded(|| Ok(5), || Ok(1), |_| Ok((0, 5))),
            );
            assert_eq!(
                value.full_text_selected,
                if mode == "retained" { Some(true) } else { None }
            );
        }
    }
    #[test]
    fn selection_enrichment_preserves_context_protected_no_reads_and_ambiguity_bound() {
        let mut protected: Node = serde_json::from_value(json!({
            "object":{"bus":":1.999","path":"/fixture/password"},"parent":null,"depth":0,
            "name":"[protected field]","role":"password text","role_id":40,
            "interfaces":[],"states":[],"protected":true,"action_names":[],"text_excerpt":null
        }))
        .unwrap();
        protected.protected = true;
        protected.interfaces = vec![TEXT.into()];
        protected.states = vec![(1 << 8) | (1 << 12) | (1 << 25)];
        let mut focused = protected.clone();
        focused.protected = false;
        focused.object.path = "/fixture/text".into();
        let original = serde_json::to_value(&focused).unwrap();
        let calls = std::cell::Cell::new(0);
        let mut nodes = vec![protected.clone(), focused.clone()];
        add_selection_readback(&mut nodes, false, |_| {
            calls.set(calls.get() + 1);
            read_selection_bounded(|| Ok(5), || Ok(1), |_| Ok((0, 5)))
        });
        assert_eq!(calls.get(), 1);
        assert_eq!(
            nodes[0].text_selection.as_ref().unwrap().status,
            "unsupported"
        );
        let mut after = serde_json::to_value(&nodes[1]).unwrap();
        after.as_object_mut().unwrap().remove("text_selection");
        assert_eq!(after, original);
        let mut ambiguous = vec![focused.clone(), focused.clone()];
        add_selection_readback(&mut ambiguous, false, |_| {
            panic!("ambiguous focused targets cannot be queried")
        });
        assert!(ambiguous.iter().all(|node| node
            .text_selection
            .as_ref()
            .unwrap()
            .reason
            .as_deref()
            == Some("focused_text_identity_ambiguous")));
        let mut expired = vec![focused];
        add_selection_readback(&mut expired, true, |_| {
            panic!("no calls after soft observation deadline")
        });
        assert_eq!(
            expired[0]
                .text_selection
                .as_ref()
                .unwrap()
                .full_text_selected,
            None
        );
    }

    #[test]
    fn selection_dbus_body_has_two_int32_outputs_and_bounded_call_count() {
        let message = zbus::message::Message::signal("/fixture", "org.fixture.Text", "Selection")
            .unwrap()
            .build(&(0i32, 5i32))
            .unwrap();
        assert_eq!(message.body().deserialize::<(i32, i32)>().unwrap(), (0, 5));
        assert_eq!(message.body().len(), 8);
        assert!(message.body().deserialize::<(String, i32)>().is_err());
        let calls = std::cell::Cell::new(0);
        let value = read_selection_bounded(
            || {
                calls.set(calls.get() + 1);
                Ok(5)
            },
            || {
                calls.set(calls.get() + 1);
                Ok(1)
            },
            |_| {
                calls.set(calls.get() + 1);
                Ok((0, 5))
            },
        );
        assert_eq!(calls.get(), 6);
        assert_eq!(value.full_text_selected, Some(true));
    }
}
