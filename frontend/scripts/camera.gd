extends Camera2D
## 自由镜头: 方向键/WASD 平移, 滚轮缩放, 空格循环跟随 NPC, Esc 取消跟随

var target: Node2D = null

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			zoom = (zoom * 1.12).clamp(Vector2.ONE * 0.8, Vector2.ONE * 5.0)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			zoom = (zoom / 1.12).clamp(Vector2.ONE * 0.8, Vector2.ONE * 5.0)
	elif event.is_action_pressed("ui_cancel"):
		set_target(null)
	elif event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_SPACE:
			set_target(get_parent().cycle_follow())

func set_target(t) -> void:
	target = t

func _process(delta: float) -> void:
	if target != null and is_instance_valid(target):
		global_position = global_position.lerp(
			target.global_position, minf(1.0, delta * 6.0))
		return
	var v := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
	global_position += v * 520.0 * delta / zoom.x
