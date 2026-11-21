extends Camera2D
## 左键拖地图，滚轮缩放，空格依次跟随 NPC，Esc 返回自由镜头。

var target: Node2D = null

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			zoom = (zoom * 1.12).clamp(Vector2.ONE * 0.8, Vector2.ONE * 5.0)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			zoom = (zoom / 1.12).clamp(Vector2.ONE * 0.8, Vector2.ONE * 5.0)
	elif event.is_action_pressed("ui_cancel"):
		get_parent().cancel_follow()
	elif event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_SPACE:
		get_parent().cycle_follow()

func set_target(node: Node2D) -> void:
	target = node

func pan(offset: Vector2) -> void:
	global_position = (global_position - offset / zoom).clamp(
		Vector2(limit_left, limit_top), Vector2(limit_right, limit_bottom))
	force_update_scroll()

func _process(delta: float) -> void:
	if is_instance_valid(target):
		global_position = global_position.lerp(target.global_position, minf(1.0, delta * 6.0))
