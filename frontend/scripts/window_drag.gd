extends Node
## Drag a panel by its title without forwarding the gesture to the map.
var window: Control
var dragging := false
var _offset := Vector2.ZERO
var _position := Vector2.ZERO
var _allow_offscreen := false

func bind(panel: Control, handle: Control, allow_offscreen := false) -> void:
	window = panel
	_position = panel.position
	_allow_offscreen = allow_offscreen
	process_mode = Node.PROCESS_MODE_ALWAYS
	handle.mouse_filter = Control.MOUSE_FILTER_STOP
	handle.mouse_default_cursor_shape = Control.CURSOR_MOVE
	handle.tooltip_text = "按住并拖动标题栏"
	handle.gui_input.connect(_begin.bind(handle))
	window.resized.connect(clamp_position)
	get_viewport().size_changed.connect(clamp_position)
	clamp_position.call_deferred()

func clamp_position() -> void:
	var viewport := get_viewport().get_visible_rect().size
	if _allow_offscreen:
		# 保留一小段标题栏，移出屏幕之后仍可拖回。
		window.position = _position.clamp(Vector2(64.0 - window.size.x, -24.0), viewport - Vector2(64.0, 34.0))
	else:
		window.position = _position.clamp(Vector2.ZERO, (viewport - window.size).max(Vector2.ZERO))

func _begin(event: InputEvent, handle: Control) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
		dragging = true
		_offset = event.global_position - window.position
		handle.accept_event()

func _input(event: InputEvent) -> void:
	if not dragging:
		return
	if not window.is_visible_in_tree():
		dragging = false
		return
	if event is InputEventMouseMotion:
		if not event.button_mask & MOUSE_BUTTON_MASK_LEFT:
			dragging = false
			return
		_position = event.position - _offset
		clamp_position()
		_position = window.position
		get_viewport().set_input_as_handled()
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if not event.pressed:
			dragging = false
		get_viewport().set_input_as_handled()
