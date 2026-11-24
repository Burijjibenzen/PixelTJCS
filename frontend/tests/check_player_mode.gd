extends SceneTree

func _initialize() -> void:
	call_deferred("_check")

func press(key: Key, pressed: bool, echo := false, shift := false) -> void:
	var event := InputEventKey.new()
	event.keycode = key
	event.pressed = pressed
	event.echo = echo
	event.shift_pressed = shift
	root.push_input(event)

func _check() -> void:
	var game := root.get_node("Game")
	var api := root.get_node("ApiClient")
	api.set_process(false)
	var scene := load("res://scenes/main.tscn") as PackedScene
	var main := scene.instantiate()
	root.add_child(main)
	current_scene = main
	main.set_physics_process(false)
	main.player_ui.set_process(false)
	for actor in main.npcs:
		actor.set_physics_process(false)
		actor.social_busy = true
		actor.global_position += Vector2(0, 1000)
	await process_frame
	assert(main.player_avatar != null)
	assert(main.player_avatar.sprite.sprite_frames.resource_path == "res://scenes/char_5.tres")
	assert(main.player_avatar.sprite.modulate == Color.WHITE)
	assert(not main.player_avatar.visible)
	assert(main.get_node_or_null("MechanicsPanel") != null)
	main.player_ui.active = true
	await process_frame
	assert(main.player_avatar.visible)
	var npc = main.npcs[0]
	npc.social_busy = true
	npc.map_id = game.FLOOR3
	npc.global_position = game.world_of(Vector2i(60, 33))
	main.player_avatar.global_position = npc.global_position + Vector2(96, 0)
	api.backend_online = true
	assert(main._near_npc(npc.npc_name))
	assert(main._nearby_target().npc == npc)
	await process_frame
	await process_frame
	assert(main.interaction_marks.sprites.has(npc.sprite))
	assert(main.interaction_marks.labels.any(func(label: Dictionary) -> bool: return label.text == "按E交互"))
	var previous_zoom: Vector2 = main.camera.zoom
	var previous_target: Node2D = main.camera.target
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.player_ui.engage_target == npc.npc_name)
	assert(npc.player_chat_hold)
	npc.social_busy = false
	assert(npc.movement_locked())
	assert(main.player_ui.dialogue_box.visible and not main.player_ui.identity.visible)
	var dialogue: Rect2 = main.player_ui.window.get_global_rect()
	assert(dialogue.position.x > main.character_status.get_rect().end.x)
	assert(dialogue.end.y < root.get_visible_rect().end.y)
	assert(not paused and main.camera.dialogue_framing)
	var to_screen := root.get_canvas_transform()
	assert(main.camera.map_area.has_point(to_screen * npc.global_position))
	assert(main.camera.map_area.has_point(to_screen * main.player_avatar.global_position))
	await process_frame
	await process_frame
	var grab: Vector2 = main.player_ui.resize_handle.get_global_rect().get_center()
	var mouse := InputEventMouseButton.new()
	mouse.position = grab
	mouse.global_position = grab
	mouse.button_index = MOUSE_BUTTON_LEFT
	mouse.pressed = true
	root.push_input(mouse, true)
	var motion := InputEventMouseMotion.new()
	motion.position = grab - Vector2(0, 100)
	motion.global_position = motion.position
	motion.button_mask = MOUSE_BUTTON_MASK_LEFT
	root.push_input(motion, true)
	mouse = InputEventMouseButton.new()
	mouse.position = motion.position
	mouse.global_position = mouse.position
	mouse.button_index = MOUSE_BUTTON_LEFT
	root.push_input(mouse, true)
	var taller: Rect2 = main.player_ui.window.get_global_rect()
	assert(taller.size.y >= dialogue.size.y + 80)
	assert(is_equal_approx(taller.end.y, dialogue.end.y))
	assert(main.camera.map_area.end.y < taller.position.y)
	main.player_ui.conversation_npc = npc.npc_name
	main.player_ui.engage_target = ""
	assert(main.player_ui._near_selected())
	var input: TextEdit = main.player_ui.message_edit
	input.grab_focus()
	input.text = "你好"
	input.set_caret_column(2)
	press(KEY_ENTER, true, false, true)
	press(KEY_ENTER, false, false, true)
	assert(input.text == "你好\n")
	assert(main.player_ui.submitted_text.is_empty())
	main.player_ui._update_controls()
	press(KEY_ENTER, true)
	press(KEY_ENTER, false)
	assert(main.player_ui.submitted_text == "你好\n")
	assert(input.text == "你好\n")
	main.player_avatar.global_position += Vector2(1, 0)
	assert(not main._near_npc(npc.npc_name))
	assert(not main.player_ui._near_selected())
	main.player_ui._close_panel()
	assert(not main.player_ui._near_selected())
	assert(not npc.player_chat_hold)
	assert(not main.camera.dialogue_framing and main.camera.target == previous_target)
	assert(main.camera.zoom.is_equal_approx(previous_zoom))

	api.backend_online = false
	var board: Dictionary = game.floor_data[game.FLOOR3].noticeboards[0]
	main.player_avatar.global_position = game.world_of(Vector2i(29, 27))
	assert(main._nearby_target().is_empty())
	main.player_avatar.global_position = game.world_of(Vector2i(18, 32))
	assert(main._nearby_target().is_empty())
	main.player_avatar.global_position = game.world_of(Vector2i(18, 31))
	assert(main._nearby_target().board.id == board.id)
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.notice_ui.visible and main.notice_ui.board_id == board.id)
	main.notice_ui.hide()

	main.player_avatar.global_position = game.world_of(Vector2i(65, 33))
	press(KEY_LEFT, true)
	press(KEY_UP, true)
	press(KEY_LEFT, true, true)
	main._physics_process(0.2)
	assert(game.cell_of(main.player_avatar.global_position) == Vector2i(65, 32))
	press(KEY_UP, false)
	main._physics_process(0.2)
	assert(game.cell_of(main.player_avatar.global_position) == Vector2i(64, 32))
	press(KEY_LEFT, false)
	main._physics_process(0.2)
	assert(main.player_avatar.sprite.animation == &"idle_left")

	var entry: Vector2i = game.stair_entry(game.FLOOR3, "stair_west")
	main.player_avatar.global_position = game.world_of(entry + Vector2i.LEFT * 2, game.FLOOR3)
	assert(not main._nearby_target().has("stair"))
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.current_map == game.FLOOR3)
	main.player_avatar.global_position = game.world_of(entry + Vector2i.LEFT, game.FLOOR3)
	assert(main._nearby_target().stair == entry)
	main.interaction_marks._process(0.0)
	var highlighted: Array = main.interaction_marks.arrows.filter(func(arrow: Dictionary) -> bool: return arrow.highlighted)
	assert(highlighted.size() == 1 and highlighted[0].text == "上楼")
	assert(main.interaction_marks.labels.any(func(label: Dictionary) -> bool: return label.text == "按E交互"))
	press(KEY_RIGHT, true)
	main._physics_process(0.2)
	press(KEY_RIGHT, false)
	assert(game.cell_of(main.player_avatar.global_position, game.FLOOR3) == entry)
	assert(main.player_avatar.map_id == game.FLOOR3 and main.current_map == game.FLOOR3)
	press(KEY_LEFT, true)
	main._physics_process(0.2)
	press(KEY_LEFT, false)
	press(KEY_E, true)
	assert(main.player_avatar.map_id == game.FLOOR4)
	assert(main.current_map == game.FLOOR4 and main.camera.target == main.player_avatar)
	press(KEY_E, true, true)
	press(KEY_E, false)
	main._physics_process(0.2)
	assert(main.current_map == game.FLOOR4)
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.player_avatar.map_id == game.FLOOR3 and main.current_map == game.FLOOR3)
	var exits: Dictionary = game.floor_data[game.FLOOR3].walk_stairs
	var campus_exit: Vector2i = exits.keys().filter(func(cell: Vector2i) -> bool: return exits[cell].map_id == game.ZHIXING)[0]
	main.player_avatar.global_position = game.world_of(campus_exit, game.FLOOR3)
	press(KEY_E, true)
	press(KEY_E, false)
	assert(main.player_avatar.map_id == game.ZHIXING and main.current_map == game.ZHIXING)
	main._select_floor(game.MAP_IDS.find(game.FLOOR3))
	assert(main.player_ui.active and not main.player_avatar.sprite.is_visible_in_tree())
	assert(not main.player_avatar.label.is_visible_in_tree())
	main._focus_player()
	assert(main.player_avatar.sprite.is_visible_in_tree() and main.player_avatar.label.is_visible_in_tree())

	var observed = load("res://scripts/player_avatar.gd").new()
	main.get_node("Actors").add_child(observed)
	main.observed_player = observed
	api.backend_online = true
	main._sync_observed_player({"player_position": {"map_id": game.FLOOR4, "x": 100, "y": 100}}, false)
	assert(observed.visible and not observed.sprite.is_visible_in_tree())
	main._select_floor(game.MAP_IDS.find(game.FLOOR4))
	assert(observed.sprite.is_visible_in_tree() and observed.label.is_visible_in_tree())
	main._sync_observed_player({"player_position": {"map_id": game.FLOOR3, "x": 100, "y": 100}}, false)
	assert(not observed.sprite.is_visible_in_tree() and not observed.label.is_visible_in_tree())
	print("player mode smoke OK")
	quit()
