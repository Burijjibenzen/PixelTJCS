extends Camera2D
## 左键拖地图，滚轮缩放，空格依次跟随 NPC，Esc 返回自由镜头。

var target: Node2D = null
var floor_bounds := Rect2()
var map_area := Rect2()
var overview := false
var dialogue_framing := false

func set_floor(bounds: Rect2, focus: Vector2) -> void:
	floor_bounds = bounds
	overview = false
	global_position = focus
	_apply_zoom(zoom.x)

func set_map_area(area: Rect2) -> void:
	map_area = area
	if overview:
		fit_floor()
	else:
		_apply_zoom(zoom.x)

func fit_floor() -> void:
	target = null
	overview = true
	global_position = floor_bounds.get_center()
	_apply_zoom(_fit_zoom())

func _fit_zoom() -> float:
	if floor_bounds.size.x <= 0.0 or floor_bounds.size.y <= 0.0:
		return 0.05
	var available := map_area.size.max(Vector2.ONE)
	return minf(minf(available.x / floor_bounds.size.x, available.y / floor_bounds.size.y), 5.0)

func _apply_zoom(value: float) -> void:
	zoom = Vector2.ONE * clampf(value, _fit_zoom(), 5.0)
	offset = (get_viewport_rect().get_center() - map_area.get_center()) / zoom
	force_update_scroll()

func _unhandled_input(event: InputEvent) -> void:
	if dialogue_framing:return
	if event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			overview = false
			_apply_zoom(zoom.x * 1.12)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			var next_zoom := zoom.x / 1.12
			if next_zoom <= _fit_zoom():
				get_parent().cancel_follow()
				fit_floor()
			else:
				_apply_zoom(next_zoom)
	elif event.is_action_pressed("ui_cancel"):
		get_parent().cancel_follow()
	elif event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_SPACE:
		get_parent().cycle_follow()

func frame_dialogue(first: Vector2, second: Vector2) -> void:
	global_position = first.lerp(second, 0.5) - Vector2(0, 16)
	_apply_zoom(minf(2.0, minf(map_area.size.x / 200.0, map_area.size.y / 120.0)))

func set_target(node: Node2D) -> void:
	target = node
	if node != null:
		overview = false

func pan(screen_delta: Vector2) -> void:
	overview = false
	var bounds := floor_bounds.grow(32.0)
	global_position = (global_position - screen_delta / zoom).clamp(bounds.position, bounds.end)
	force_update_scroll()

func _process(delta: float) -> void:
	if is_instance_valid(target):
		global_position = global_position.lerp(target.global_position, minf(1.0, delta * 6.0))
