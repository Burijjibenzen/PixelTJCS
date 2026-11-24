extends CanvasLayer

const PALETTE := preload("res://assets/tea_theme.tres")

var _viewport: Viewport
var _input_was_disabled := false
var _spinner: Control
var _angle := 0.0

func _ready() -> void:
	layer = 100
	process_mode = Node.PROCESS_MODE_ALWAYS
	_viewport = get_viewport()
	_input_was_disabled = _viewport.is_input_disabled()
	_viewport.set_disable_input(true)

	var shade := ColorRect.new()
	shade.color = Color("#1a1612b8")
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(shade)

	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.add_child(center)
	var card := PanelContainer.new()
	card.theme = PALETTE
	center.add_child(card)
	var surface := StyleBoxFlat.new()
	surface.bg_color = Color("#f3e9dc")
	surface.border_color = Color("#976d4c")
	surface.set_border_width_all(2)
	surface.set_corner_radius_all(9)
	surface.set_content_margin_all(20)
	card.add_theme_stylebox_override("panel", surface)
	var content := VBoxContainer.new()
	content.custom_minimum_size.x = 180
	content.add_theme_constant_override("separation", 12)
	card.add_child(content)

	_spinner = Control.new()
	_spinner.custom_minimum_size.y = 38
	_spinner.draw.connect(_draw_spinner)
	content.add_child(_spinner)
	var title := Label.new()
	title.text = "世界重置中"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size", 22)
	title.add_theme_color_override("font_color", Color("#35291f"))
	content.add_child(title)

func _process(delta: float) -> void:
	_angle = fposmod(_angle + delta * TAU, TAU)
	_spinner.queue_redraw()

func _draw_spinner() -> void:
	var center := _spinner.size / 2.0
	_spinner.draw_arc(center, 14.0, 0.0, TAU, 32, Color("#d7c1ab"), 3.0, true)
	_spinner.draw_arc(center, 14.0, _angle, _angle + TAU * 0.68, 32, Color("#8c512e"), 3.0, true)

func _exit_tree() -> void:
	if is_instance_valid(_viewport):
		_viewport.set_disable_input(_input_was_disabled)
