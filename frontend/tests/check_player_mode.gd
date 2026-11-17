extends SceneTree

func _initialize() -> void:
	call_deferred("_check")

func _check() -> void:
	var scene := load("res://scenes/main.tscn") as PackedScene
	var main := scene.instantiate()
	root.add_child(main)
	current_scene = main
	await process_frame
	assert(main.player_avatar != null)
	assert(not main.player_avatar.visible)
	assert(main.get_node_or_null("MechanicsPanel") != null)
	main.player_ui.active = true
	await process_frame
	assert(main.player_avatar.visible)
	var npc = main.npcs[0]
	main.player_avatar.map_id = npc.map_id
	main.player_avatar.global_position = npc.global_position
	assert(main._near_npc(npc.npc_name))
	main.player_ui.open_for(npc.npc_name)
	assert(main.player_ui.engage_target == npc.npc_name)
	main.player_ui.conversation_npc = npc.npc_name
	main.player_ui.engage_target = ""
	assert(main.player_ui._near_selected())
	main.player_avatar.global_position += Vector2(80, 0)
	assert(not main._near_npc(npc.npc_name))
	assert(not main.player_ui._near_selected())
	main.player_ui._close_panel()
	assert(not main.player_ui._near_selected())
	var game = root.get_node("Game")
	main.stair_options = [{"map_id": game.FLOOR4, "cell": game.stair_entry(game.FLOOR4, "stair_west")}]
	main._choose_player_stair(0)
	assert(main.player_avatar.map_id == game.FLOOR4)
	assert(main.current_map == game.FLOOR4)
	print("player mode smoke OK")
	quit()
