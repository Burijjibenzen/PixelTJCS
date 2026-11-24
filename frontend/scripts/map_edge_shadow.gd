extends Sprite2D

const SHADOW_COLOR := Color(0.025, 0.035, 0.055, 0.82)
const SHADOW_DROP_TILES := 0.45

func setup(floor_node: Node2D, floor: Dictionary) -> void:
	var wall := floor_node.get_node("wall") as TileMapLayer
	var bounds := wall.get_used_rect()
	for room: Dictionary in floor.rooms.values():
		var layer := floor_node.get_node(NodePath(room.layer)) as TileMapLayer
		bounds = bounds.merge(layer.get_used_rect())
	bounds = bounds.grow(4)

	# Bake the soft silhouette once at one sample per tile.
	var mask := PackedFloat32Array()
	mask.resize(bounds.size.x * bounds.size.y)
	for cell in wall.get_used_cells():
		mask[_cell_index(cell, bounds)] = 1.0
	for cells: Array in floor.floor_cells.values():
		for cell: Vector2i in cells:
			mask[_cell_index(cell, bounds)] = 1.0

	name = "MapEdgeShadow"
	texture = ImageTexture.create_from_image(_shadow_image(mask, bounds.size))
	texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR
	centered = false
	z_index = -1
	var tile_size := Vector2(wall.tile_set.tile_size)
	scale = tile_size
	position = wall.position + wall.map_to_local(bounds.position) - tile_size * 0.5

func _cell_index(cell: Vector2i, bounds: Rect2i) -> int:
	var point := cell - bounds.position
	return point.y * bounds.size.x + point.x

func _shadow_image(mask: PackedFloat32Array, dimensions: Vector2i) -> Image:
	var softened := _blur(_blur(mask, dimensions, 1), dimensions, dimensions.x)
	var image := Image.create_empty(dimensions.x, dimensions.y, false, Image.FORMAT_RGBA8)
	for y in range(dimensions.y):
		for x in range(dimensions.x):
			var i := y * dimensions.x + x
			if mask[i] > 0.0:
				continue
			var color := SHADOW_COLOR
			var above := maxi(0, y - 1) * dimensions.x + x
			color.a *= lerpf(softened[i], softened[above], SHADOW_DROP_TILES)
			image.set_pixel(x, y, color)
	return image

func _blur(source: PackedFloat32Array, dimensions: Vector2i, stride: int) -> PackedFloat32Array:
	var result := PackedFloat32Array()
	result.resize(source.size())
	for y in range(2, dimensions.y - 2):
		for x in range(2, dimensions.x - 2):
			var i := y * dimensions.x + x
			result[i] = (source[i - 2 * stride] + 4.0 * source[i - stride] + 6.0 * source[i]
				+ 4.0 * source[i + stride] + source[i + 2 * stride]) / 16.0
	return result
