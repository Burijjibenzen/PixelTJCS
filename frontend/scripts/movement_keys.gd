extends Node
## 后按的方向优先，松开后恢复仍按住的方向。

const DIRECTIONS := {
	KEY_LEFT: Vector2i.LEFT, KEY_A: Vector2i.LEFT,
	KEY_RIGHT: Vector2i.RIGHT, KEY_D: Vector2i.RIGHT,
	KEY_UP: Vector2i.UP, KEY_W: Vector2i.UP,
	KEY_DOWN: Vector2i.DOWN, KEY_S: Vector2i.DOWN,
}
const ANIMATIONS := {
	Vector2i.ZERO: "idle",
	Vector2i.LEFT: "walk_left", Vector2i.RIGHT: "walk_right",
	Vector2i.UP: "walk_up", Vector2i.DOWN: "walk_down",
}

var held: Array[int] = []

func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS

func _input(event: InputEvent) -> void:
	if not event is InputEventKey or event.echo or not DIRECTIONS.has(event.keycode):
		return
	held.erase(event.keycode)
	if event.pressed:
		held.append(event.keycode)

func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		held.clear()

func direction() -> Vector2i:
	return Vector2i.ZERO if held.is_empty() else DIRECTIONS[held.back()]
