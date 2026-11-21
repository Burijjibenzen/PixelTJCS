extends SceneTree
## M3: 真实 TCP 经过 SocialUI 收包入口，同时验证日志、头顶气泡、首次补发。
class Actor extends Node2D:
	var npc_name: String
func _initialize() -> void:
	call_deferred("verify")
func verify() -> void:
	root.get_node("ApiClient").set_process(false)
	var url := OS.get_environment("M3_TEST_WS_URL")
	var ui = load("res://scripts/social_ui.gd").new()
	var actors: Array = []
	for name in JSON.parse_string(OS.get_environment("M3_TEST_NAMES")):
		var actor := Actor.new()
		actor.npc_name = name
		root.add_child(actor)
		actors.append(actor)
	ui.setup(actors)
	root.add_child(ui)
	ui.set_process(false)
	ui.profile_panel.set_process(false)
	ui.socket.connect_to_url(url + "/ws/events?after=0")
	var deadline := Time.get_ticks_msec() + 30000
	var started := false
	var request := HTTPRequest.new()
	root.add_child(request)
	while Time.get_ticks_msec() < deadline:
		ui._poll_socket(0.03)
		if ui.stream_online and not started:
			if not "连接前的台词" in ui.dialogue_log.text:
				await create_timer(0.03).timeout
				continue
			if not ui.playbacks.is_empty():
				push_error("历史补发不应播放过期气泡")
				quit(2)
				return
			started = true
			request.request(url.replace("ws://", "http://") + "/test/start", [], HTTPClient.METHOD_POST)
		for key in ui.live_versions:
			if int(ui.live_versions[key]) == 6:
				if ui.dialogue_log.text.count("：") < 7 or ui.playbacks.is_empty():
					push_error("实时台词未同步写入日志和气泡")
					quit(3)
					return
				var speaker = ui.playbacks.back().event.result.transcript[0].speaker
				if not ui.bubble_labels[speaker].visible:
					quit(4)
					return
				print("PASS: actual SocialUI WebSocket receives history and six live lines; log and head bubbles visible")
				ui.socket.close()
				quit(0)
				return
		await create_timer(0.03).timeout
	push_error("SocialUI WebSocket smoke timed out")
	quit(5)
