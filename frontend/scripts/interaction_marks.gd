extends Node2D
## 在地图上方绘制固定屏幕尺寸的提示和白边，供主地图和试走共用。

const PIXEL_FONT := preload("res://assets/fonts/fusion-pixel-12px-proportional-zh_hans.otf.woff2")
const FONT_SIZE := 24
const OUTLINE_WIDTH := 2.0
const PROMPT := "按E交互"

var sprites: Array[AnimatedSprite2D] = []
var footprints: Array[PackedVector2Array] = []
var labels: Array[Dictionary] = []
var arrows: Array[Dictionary] = []
var clip := Rect2()
var pointer := Vector2.INF
var populate: Callable
var _contours := {}

func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	process_priority = 10
	get_window().mouse_exited.connect(func() -> void: pointer = Vector2.INF)

func _input(event: InputEvent) -> void:
	if event is InputEventMouse:
		pointer = event.position

func _process(_delta: float) -> void:
	sprites.clear()
	footprints.clear()
	labels.clear()
	arrows.clear()
	populate.call()
	queue_redraw()

func show_prompt(point: Vector2) -> void:
	var height := PIXEL_FONT.get_height(FONT_SIZE) + 4.0
	var prompt_width := PIXEL_FONT.get_string_size(PROMPT, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x
	for label: Dictionary in labels:
		var gap: Vector2 = label.point - point
		var label_width := PIXEL_FONT.get_string_size(label.text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x
		if absf(gap.y) < height and absf(gap.x) < (prompt_width + label_width) * 0.5 + 4.0:
			label.point.y = point.y - height
	labels.append({"text": PROMPT, "point": point})

func outline_cells(cells: Array, offset: Vector2, transform: Transform2D) -> void:
	var occupied := {}
	for raw: Array in cells:
		occupied[Vector2i(int(raw[0]), int(raw[1]))] = true
	var edges := PackedVector2Array()
	for cell: Vector2i in occupied:
		var left_top := offset + Vector2(cell) * Game.tile_size
		var x := Vector2(Game.tile_size, 0)
		var y := Vector2(0, Game.tile_size)
		if not occupied.has(cell + Vector2i.UP):
			edges.append_array([transform * left_top, transform * (left_top + x)])
		if not occupied.has(cell + Vector2i.DOWN):
			edges.append_array([transform * (left_top + y), transform * (left_top + x + y)])
		if not occupied.has(cell + Vector2i.LEFT):
			edges.append_array([transform * left_top, transform * (left_top + y)])
		if not occupied.has(cell + Vector2i.RIGHT):
			edges.append_array([transform * (left_top + x), transform * (left_top + x + y)])
	footprints.append(edges)

func _draw() -> void:
	# 屏幕缩放只影响地图，不改变各类交互描边的相对粗细。
	var width := OUTLINE_WIDTH / get_screen_transform().x.length()
	for sprite: AnimatedSprite2D in sprites:
		_draw_sprite_outline(sprite, width)
	for edges: PackedVector2Array in footprints:
		for i in range(0, edges.size(), 2):
			var extra := edges[i].direction_to(edges[i + 1]) * width * 0.5
			draw_line(edges[i] - extra, edges[i + 1] + extra, Color.WHITE, width)
	for label: Dictionary in labels:
		_draw_text(label.text, label.point, FONT_SIZE)
	for arrow: Dictionary in arrows:
		var center: Vector2 = arrow.point.round()
		if not clip.has_point(center):
			continue
		var highlighted: bool = arrow.get("highlighted", false)
		var ink := Color("#ffe49a") if highlighted else Color("#88e9ff")
		draw_rect(Rect2(center - Vector2(8, 8), Vector2(16, 16)), Color("#24364b"))
		if highlighted:
			draw_rect(Rect2(center - Vector2(9, 9), Vector2(18, 18)), Color.WHITE, false, width)
		draw_rect(Rect2(center - Vector2(7, 7), Vector2(14, 14)), ink, false, 1.0)
		draw_rect(Rect2(center + Vector2(-1, -5), Vector2(2, 6)), ink)
		draw_colored_polygon(PackedVector2Array([
			center + Vector2(-4, 0), center + Vector2(4, 0), center + Vector2(0, 5),
		]), ink)
		_draw_text(arrow.text, center + Vector2(11, 5), 12, false)

func _draw_sprite_outline(sprite: AnimatedSprite2D, width: float) -> void:
	var texture := sprite.sprite_frames.get_frame_texture(sprite.animation, sprite.frame)
	if not _contours.has(texture):
		var image := texture.get_image()
		if image == null: # 无窗口验证使用虚拟渲染器，无法读取画面像素。
			_contours[texture] = []
			return
		var alpha := BitMap.new()
		alpha.create_from_image_alpha(image)
		_contours[texture] = alpha.opaque_to_polygons(Rect2i(Vector2i.ZERO, alpha.get_size()), 0.5)
	var to_marks := get_global_transform_with_canvas().affine_inverse() * sprite.get_global_transform_with_canvas()
	var size := texture.get_size()
	var origin := sprite.offset - (size * 0.5 if sprite.centered else Vector2.ZERO)
	for contour: PackedVector2Array in _contours[texture]:
		var outline := PackedVector2Array()
		for point: Vector2 in contour:
			if sprite.flip_h:
				point.x = size.x - point.x
			if sprite.flip_v:
				point.y = size.y - point.y
			outline.append(to_marks * (origin + point))
		outline.append(outline[0])
		draw_polyline(outline, Color.WHITE, width)

func _draw_text(text: String, point: Vector2, font_size: int, centered := true) -> void:
	if not clip.has_point(point):
		return
	var origin := point.round()
	if centered:
		origin.x -= roundf(PIXEL_FONT.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x / 2.0)
	draw_string_outline(PIXEL_FONT, origin, text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size, 3, Color("#1a222c"))
	draw_string(PIXEL_FONT, origin, text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size, Color.WHITE)
