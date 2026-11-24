extends Control

const PANELS: Array[Texture2D] = [
	preload("res://assets/bg_img/1.jpg"),
	preload("res://assets/bg_img/2.jpg"),
	preload("res://assets/bg_img/3.jpg"),
	preload("res://assets/bg_img/4.jpg"),
]
const SCROLL_SPEED := 14.0
const SHADE := Color(0.055, 0.075, 0.105, 0.44)

var _offset_in_panels := 0.0

func _ready() -> void:
	visible = ApiClient.web_mode
	set_process(visible)

func _panel_width() -> float:
	return maxf(1.0, ceilf(size.y * PANELS[0].get_width() / PANELS[0].get_height()))

func _process(delta: float) -> void:
	_offset_in_panels = fposmod(_offset_in_panels + delta * SCROLL_SPEED / _panel_width(), PANELS.size())
	queue_redraw()

func _draw() -> void:
	var width := _panel_width()
	var offset := _offset_in_panels * width
	var first := floori(-offset / width)
	var end := ceili((size.x - offset) / width)
	for index in range(first, end):
		var left := offset + index * width
		var texture := PANELS[posmod(index, PANELS.size())]
		draw_texture_rect(texture, Rect2(left, 0, width, size.y), false)
	draw_rect(Rect2(Vector2.ZERO, size), SHADE)
