extends SceneTree
## M3: 无模型、无业务数据的前端事件契约回归。
class Actor extends Node2D:
	var npc_name: String
	var social_playing := false
func _initialize() -> void:
	call_deferred("verify")
func check(ok: bool, label: String) -> bool:
	if not ok:
		push_error("FAIL: " + label)
		quit(1)
	return ok
func verify() -> void:
	root.get_node("ApiClient").set_process(false)
	var ui = load("res://scripts/social_ui.gd").new()
	var actors: Array = []
	for name in ["A", "B", "C", "D"]:
		var actor := Actor.new()
		actor.npc_name = name
		root.add_child(actor)
		actors.append(actor)
	ui.setup(actors)
	root.add_child(ui)
	ui.set_process(false)
	ui.history_pending = true
	ui._accept_stream({"kind": "snapshot", "id": 50, "epoch": "test", "live": [], "world": {"npcs": {}, "time": "08:00"}})
	var first := {"kind": "dialogue_line", "id": 1, "conversation_id": "one", "sequence": 1, "participants": ["A", "B"], "speaker": "A", "text": "你好"}
	var second := {"kind": "dialogue_line", "id": 2, "conversation_id": "two", "sequence": 1, "participants": ["C", "D"], "speaker": "C", "text": "另一场对话"}
	ui._accept_stream(first)
	# M3: 世界快照编号不能让尚未收到的台词被判作重复事件。
	if not check("你好" in ui.dialogue_log.text, "snapshot must not skip pending dialogue history"): return
	ui._accept_stream(second)
	if not check(ui.playbacks.size() == 2, "independent concurrent playback"): return
	ui._accept_stream(second)
	if not check(ui.playbacks.size() == 2, "event id deduplication"): return
	ui.playbacks[0].remaining = 0
	ui._advance_turn(ui.playbacks[0])
	if not check(not actors[0].social_playing and actors[2].social_playing, "independent movement release"): return
	var long_line := first.duplicate(true)
	long_line.id = 3
	long_line.sequence = 2
	long_line.text = "长台词分段测试内容。".repeat(20)
	ui._accept_stream(long_line)
	var playback = ui.playbacks.back()
	if not check(playback.remaining == 10.0 and playback.event.result.transcript.size() > 1, "ten second segments"): return
	var next_line := first.duplicate(true)
	next_line.id = 4
	next_line.sequence = 3
	next_line.speaker = "B"
	next_line.text = "立即替换"
	ui._accept_stream(next_line)
	if not check(not ui.bubble_labels["A"].visible and ui.bubble_labels["B"].visible, "immediate replacement"): return
	ui._accept_stream({"kind": "conversation_released", "id": 5, "conversation_id": "one"})
	playback = ui.playbacks.back()
	ui._advance_turn(playback)
	if not check(not ui.bubble_labels["B"].visible, "conversation release"): return
	ui._accept_stream(dict_replay(first, 6))
	if not check(not ui.bubble_labels["A"].visible, "history replay does not replay expired bubbles"): return
	ui._draw_relations([{"a": "A", "b": "B", "score": 70, "level": "好朋友", "reason": "帮助实验", "delta": 3}])
	if not check(ui.relation_grid.get_child_count() == 25, "relation matrix"): return
	ui.history_name = "A"
	ui._history_done(HTTPRequest.RESULT_SUCCESS, 200, PackedStringArray(), JSON.stringify([{"id": 10, "game_time": 480}, {"id": 12, "game_time": 900}]).to_utf8_buffer())
	if not check(ui.history_items.size() == 2 and ui.history_slider.value == 2, "history timeline"): return
	await process_frame
	await process_frame
	var bubble = ui.bubble_labels["C"]
	if not check(bubble.position.y + bubble.size.y <= -88, "bubble above head"): return
	if not check(ui.panel.position.y + ui.panel.size.y <= 720, "panel fits viewport"): return
	print("PASS: WS event contract, dedup, concurrent bubbles, ten-second segments, replacement, history, matrix, layout")
	quit(0)
func dict_replay(event: Dictionary, id: int) -> Dictionary:
	var copy := event.duplicate(true)
	copy.id = id
	copy.replay = true
	return copy
