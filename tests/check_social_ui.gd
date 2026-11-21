extends SceneTree
## M3: 无模型、无业务数据的前端事件契约回归。
class Actor extends Node2D:
	var npc_name: String
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
	ui.profile_panel.set_process(false)
	ui._accept_stream({"kind": "snapshot", "id": 50, "epoch": "test", "live": [], "world": {"npcs": {}, "time": "08:00"}})
	var first := {"kind": "dialogue_line", "id": 1, "conversation_id": "one", "game_event_id": "conversation:one", "speech": "npc_dialogue", "sequence": 1, "participants": ["A", "B"], "speaker": "A", "text": "你好"}
	var second := {"kind": "dialogue_line", "id": 2, "conversation_id": "two", "game_event_id": "conversation:two", "speech": "npc_dialogue", "sequence": 1, "participants": ["C", "D"], "speaker": "C", "text": "另一场对话"}
	ui._accept_stream(first)
	# M3: 世界快照编号不能让尚未收到的台词被判作重复事件。
	if not check("你好" in ui.dialogue_log.text, "snapshot must not skip pending dialogue history"): return
	ui._accept_stream(second)
	if not check(ui.playbacks.size() == 2, "independent concurrent playback"): return
	if not check(not "另一场对话" in ui.dialogue_log.text, "the selected character log excludes unrelated dialogue"): return
	ui.picker.select(2)
	ui.picker.item_selected.emit(2)
	if not check("另一场对话" in ui.dialogue_log.text and not "你好" in ui.dialogue_log.text, "the common picker switches dialogue logs"): return
	ui.picker.select(0)
	ui.picker.item_selected.emit(0)
	ui._accept_stream(second)
	if not check(ui.playbacks.size() == 2, "event id deduplication"): return
	ui.playbacks[0].remaining = 0
	ui._advance_turn(ui.playbacks[0])
	if not check(not ui.bubble_labels["A"].visible and ui.bubble_labels["C"].visible, "independent concurrent displays"): return
	var long_line := first.duplicate(true)
	long_line.id = 3
	long_line.sequence = 2
	long_line.text = "长台词分段测试内容。".repeat(20)
	ui._accept_stream(long_line)
	var playback = ui.playbacks.back()
	if not check(playback.remaining >= 4.0 and playback.remaining <= 7.0 and playback.event.result.transcript.size() > 1, "long segments stay within the shorter display limit"): return
	var next_line := first.duplicate(true)
	next_line.id = 4
	next_line.sequence = 3
	next_line.speaker = "B"
	next_line.text = "恢复即时替换"
	ui._accept_stream(next_line)
	if not check(not ui.bubble_labels["A"].visible and ui.bubble_labels["B"].visible, "original immediate replacement without queue"): return
	playback = ui.playbacks.back()
	ui._accept_stream({"kind": "conversation_released", "id": 5, "conversation_id": "one"})
	if not check(ui.bubble_labels["B"].visible, "behavior release does not control bubble display"): return
	root.get_node("ApiClient").simulation_speed = 60.0
	var remaining: float = playback.remaining
	if not check(remaining >= 4.0 and remaining <= 5.0, "short lines display for four to five seconds"): return
	ui._tick_playbacks(1.0)
	if not check(is_equal_approx(playback.remaining, remaining - 1.0), "fast forward does not shorten reading time"): return
	ui._tick_playbacks(remaining - 1.0)
	if not check(not ui.bubble_labels["B"].visible, "bubble expires after its allotted real time"): return
	var final_line := next_line.duplicate(true)
	final_line.id = 6
	final_line.sequence = 1
	final_line.conversation_id = "depart"
	final_line.text = "临别前还有很长的话。".repeat(20)
	ui._accept_stream(final_line)
	if not check(ui.bubble_labels["B"].visible, "final line appears before release"): return
	ui._accept_stream({"kind": "dialogue_finished", "id": 7, "conversation_id": "depart"})
	if not check(not ui.bubble_labels["B"].visible and not ui.playbacks.any(func(p): return p.event.conversation_id == "depart"), "departure clears only finished conversation bubble"): return
	ui._accept_stream(dict_replay(first, 8))
	if not check(not ui.bubble_labels["A"].visible, "history replay does not replay expired bubbles"): return
	# 重连只恢复最新一句当前段落，不重播旧句。
	var restore := first.duplicate(true)
	restore.sequence = 10
	restore.text = "当前句"
	restore.segments = [{"text": "已读部分", "seconds": 10.0}, {"text": "正在阅读", "seconds": 10.0}]
	restore.playback_elapsed = 12.0
	ui._show_line(restore)
	playback = ui.playbacks.back()
	if not check(ui.bubble_labels["A"].get_node("Text").text == "正在阅读" and is_equal_approx(playback.remaining, 8.0), "reconnect restores current segment"): return
	ui._accept_stream({"kind": "error", "id": 9, "conversation_id": "one", "message": "旧错误", "replay": true})
	if not check(ui.bubble_labels["A"].visible, "replayed old error cannot cancel restored dialogue"): return
	ui._accept_stream({"kind": "error", "id": 10, "conversation_id": "one", "message": "测试取消"})
	if not check(not ui.bubble_labels["A"].visible, "error clears only affected display"): return
	ui._draw_relations([{"a": "A", "b": "B", "score": 70, "level": "好朋友", "reason": "帮助实验", "delta": 3}])
	if not check(ui.relation_grid.get_child_count() == 25, "relation matrix"): return
	var relation_view: RichTextLabel = ui.views[3]
	var relation_scroll := relation_view.get_parent().get_parent() as ScrollContainer
	ui.tabs.current_tab = 3
	relation_view.text = "关系变化记录\n".repeat(60)
	await process_frame
	await process_frame
	await process_frame
	var scrollbar := relation_scroll.get_v_scroll_bar()
	if not check(relation_view.fit_content and not relation_view.scroll_active and scrollbar.max_value > scrollbar.page, "relation matrix and changes share one outer vertical scrollbar"): return
	if not check(ui.picker.get_parent().get_child_count() == 3 and ui.tabs.get_index() == ui.picker.get_index() + 1,
			"observer uses one shared character picker directly above the tabs"): return
	ui.panel_name = "A"
	ui.fetching = "memory"
	var memory := {"content": "测试记录", "type": "dialogue", "strength": 0.5, "game_time": 510, "forgotten_at": 2070}
	var insight := memory.duplicate()
	insight.type = "reflection"
	ui._panel_done(HTTPRequest.RESULT_SUCCESS, 200, PackedStringArray(), JSON.stringify({"as_of": 630, "active": [memory], "forgotten": [memory], "reflections": [insight]}).to_utf8_buffer())
	for view in ui.views.slice(0, 3):
		if not check("发生于：第 1 天 08:30" in view.text and "距今：2 小时" in view.text and "50%" in view.text and "■■■■■" in view.text, "all memory tabs show occurrence/elapsed time without changing strength bars"): return
		if not check(view.get_parent() is ScrollContainer and not view.scroll_active, "memory pages have a single outer scrollbar"): return
	if not check("遗忘于：第 2 天 10:30" in ui.views[1].text, "forgotten memories show their forgetting date"): return
	if not check(ui._format_game_duration(0) == "0 分钟" and ui._format_game_duration(59.9) == "59 分钟" and ui._format_game_duration(60) == "1 小时" and ui._format_game_duration(4320) == "3 天" and ui._format_game_duration(1501) == "1 天 1 小时 1 分钟", "duration format handles minute/hour/day boundaries"): return
	if not check(ui._format_game_timestamp(1439) == "第 1 天 23:59" and ui._format_game_timestamp(1440) == "第 2 天 00:00" and ui._format_game_timestamp(0) == "第 1 天 00:00", "timestamps follow the game clock day boundaries"): return
	var timeline = ui.timeline_panel
	timeline._dirty = false
	ui._accept_stream({"kind": "game_event_recorded", "id": 11, "actors": ["B", "C"]})
	if not check(not timeline._dirty, "a third party's event does not refresh the selected actor"): return
	ui._accept_stream({"kind": "game_event_recorded", "id": 12, "actors": ["A", "B"], "replay": true})
	if not check(timeline._dirty, "replayed event changes refresh the participant's timeline after reconnect"): return
	var api := root.get_node("ApiClient")
	api.last_world["npcs"] = {"A": {"current_event": {"kind": "conversation", "phase": "travelling", "title": "找 B 交谈"}}}
	timeline._update_current()
	if not check(timeline._current.visible and "找 B 交谈" in timeline._current.text and timeline.entries.get_child_count() == 0,
			"running event is visible independently of completed history"): return
	api.last_world.npcs["A"] = {"encounter_wait_for": "B", "current_event": {"kind": "encounter_wait",
		"phase": "waiting", "tier": "medium", "title": "被B寻找", "schedule_reason": "对方已进入相遇范围"}}
	timeline._update_current()
	if not check("被B寻找" in timeline._current.text and "中档 · 对方已进入相遇范围" in timeline._current.text,
			"the recipient sees the encounter as a distinct medium-tier event"): return
	var pending = timeline._append({
		"kind": "encounter_wait", "phase": "scheduled", "actor": "A", "target": {"id": "B"},
		"game_time": 510, "location": {"map_id": "jishilou_floor3", "room_id": "R01"},
		"content": {"title": "被B寻找", "tier": "medium"}, "result": {},
	})
	if not check("被B寻找 · 中档待办" in pending.get_node("Body/Summary/Inset/Fields").get_parsed_text(),
			"a queued encounter appears in the recipient timeline before it takes focus"): return
	var card = timeline._append({
		"kind": "conversation_completed", "actor": "A", "target": {"id": "B"},
		"game_time": 510, "location": {"map_id": "jishilou_floor3", "room_id": "R01"},
		"content": {"transcript": [{"speaker": "A", "text": "聊过实验进展"}]},
		"result": {"relation": {"score": 71, "reason": "分享近况"}, "gossip": {}},
		"reaction": {"action": "continue", "reason": "当前结果没有需要调整的日程事项"},
	})
	var summary: Button = card.get_node("Body/Summary/Toggle")
	var details: RichTextLabel = card.get_node("Body/Details")
	var fields: String = card.get_node("Body/Summary/Inset/Fields").get_parsed_text()
	if not check(not details.visible and "时间" in fields and "地点" in fields and "行为" in fields and not "聊过实验进展" in fields, "timeline only shows the three summary fields before expansion"): return
	summary.button_pressed = true
	if not check(details.visible and timeline._card_hover.border_color == Color.WHITE and timeline._card_hover.border_width_top == 2, "expanded content and two-pixel hover border are available"): return
	var text := fields + details.get_parsed_text()
	if not check("A ↔ B" in text and "聊过实验进展" in text and "好感度 71" in text and "继续原计划" in text, "conversation transcript, outcome and agent reaction render in the timeline"): return
	card = timeline._append({
		"kind": "activity", "actor": "A", "target": {"id": "R03"},
		"game_time": 530, "ended_at": 565, "location": {"map_id": "jishilou_floor3", "room_id": "R03"},
		"content": {"title": "查阅资料", "purpose": "理解当前方法", "tier": "medium",
			"schedule_notes": [{"text": "实验遇到问题，暂挂科研去查资料"}, {"text": "查阅结束，恢复科研"}]},
		"result": {"detail": "已完成实际到达后的停留"},
		"assessment": {"status": "decided", "outcome": "还需要验证", "reason": "记录缺少数据",
			"action": {"type": "perform_at", "purpose": "去实验室验证"}},
	})
	text = expand_event(card)
	if not check("执行结果" in text and "还需要验证" in text and "去实验室验证" in text, "activity facts, model outcome and follow-up render in the timeline"): return
	if not check("中档 · 实验遇到问题，暂挂科研去查资料" in text and "查阅结束，恢复科研" in text,
			"timeline shows tier and concrete pause/resume reasons"): return
	await process_frame
	await process_frame
	var bubble = ui.bubble_labels["C"]
	if not check(bubble.position.y + bubble.size.y <= -88, "bubble above head"): return
	if not check(ui.panel.position.y + ui.panel.size.y <= 720, "panel fits viewport"): return
	var solo := {"conversation_id": "visit:self_talk", "game_event_id": "conversation:visit", "speech": "event_self_talk",
		"sequence": 1, "participants": ["A"], "speaker": "A", "text": "（自言自语）找 B 聊聊吧。"}
	ui._show_line(solo)
	if not check(ui.bubble_labels["A"].get_node("Text").text == solo.text, "solo event shows before the invitation"): return
	var invite := {"kind": "dialogue_line", "id": 13, "conversation_id": "visit", "game_event_id": "conversation:visit", "speech": "npc_dialogue", "sequence": 1,
		"ordered": true, "participants": ["A", "B"], "speaker": "A", "text": "（自言自语）现在方便聊一下吗？",
		"segments": [{"text": "（自言自语）现在方便聊一下吗？", "seconds": 2.0}]}
	var decline := invite.duplicate(true)
	decline.merge({"id": 14, "sequence": 2, "speaker": "B", "text": "我等会儿要上课。",
		"segments": [{"text": "我等会儿要上课。", "seconds": 3.0}]}, true)
	ui._accept_stream(invite)
	if not check(ui.bubble_labels["A"].get_node("Text").text == "现在方便聊一下吗？" and not "自言自语" in ui.dialogue_log.text, "dialogue immediately replaces self-talk and strips its marker"): return
	solo.conversation_id = "late-self-talk"
	ui._show_line(solo)
	if not check(not ui.playbacks.any(func(p): return p.event.get("speech") == "event_self_talk"), "solo events do not queue over a two-person dialogue"): return
	ui._accept_stream(decline)
	if not check(ui.bubble_labels["A"].visible and not ui.bubble_labels["B"].visible, "invitation preserves the first line until it is readable"): return
	root.get_node("ApiClient").simulation_paused = true
	ui._tick_playbacks(20.0)
	if not check(ui.bubble_labels["A"].visible and not ui.bubble_labels["B"].visible, "paused world holds the current bubble and queue"): return
	root.get_node("ApiClient").simulation_paused = false
	ui._tick_playbacks(2.0)
	if not check(not ui.bubble_labels["A"].visible and ui.bubble_labels["B"].visible and ui.bubble_labels["B"].get_node("Text").text == decline.text, "recipient's refusal appears in their own bubble"): return
	var followup := invite.duplicate(true)
	followup.merge({"id": 16, "conversation_id": "next"}, true)
	ui._accept_stream(followup)
	if not check(ui.bubble_labels["B"].visible and not ui.bubble_labels["A"].visible, "later invitation does not replace an unread reason"): return
	ui._tick_playbacks(3.0)
	if not check(not ui.bubble_labels["B"].visible and ui.bubble_labels["A"].visible, "overlapping invitation starts after the earlier one"): return
	ui._accept_stream({"kind": "dialogue_finished", "id": 17, "conversation_id": "next"})
	if not check(not ui.bubble_labels["A"].visible, "authoritative conversation completion clears the remaining display"): return
	card = timeline._append({
		"kind": "conversation", "actor": "A", "target": {"id": "B"}, "game_time": 550,
		"location": {"map_id": "jishilou_floor3", "room_id": "R03"},
		"content": {"transcript": [{"speaker": "B", "text": decline.text}]},
		"result": {"status": "declined", "consent": {"decision": "decline"}},
	})
	text = expand_event(card)
	if not check("交谈邀约" in text and decline.text in text, "refusal transcript appears in event history"): return
	timeline.select_npc("B")
	card = timeline._append({
		"kind": "conversation", "actor": "A", "target": {"id": "B"}, "game_time": 550,
		"location": {"map_id": "jishilou_floor3", "room_id": "R03"},
		"content": {"purpose": "A 的私下计划", "transcript": [{"speaker": "B", "text": decline.text}]},
		"result": {"status": "declined", "consent": {"decision": "decline"}},
	})
	text = expand_event(card)
	if not check("收到 A 的邀约" in text and "我的回应" in text and not "A 的私下计划" in text, "recipient timeline describes the recipient's perspective"): return
	card = timeline._append({
		"kind": "conversation_overheard", "actor": "B", "target": {"id": "conversation:test"},
		"game_time": 560, "location": {"map_id": "jishilou_floor3", "room_id": "R03"},
		"content": {"title": "旁听交谈"}, "result": {"detail": "我好像听到他们说实验得重做。"},
		"source_event_id": "conversation:test", "source": null,
	})
	text = expand_event(card)
	if not check("旁听交谈" in text and "旁听印象" in text and "我好像听到" in text, "overheard memory renders as a personal impression"): return
	ui._show_line({"conversation_id": "old-comment", "sequence": 1, "participants": ["D"], "speaker": "D", "text": "公告感想", "activity": "notice_comment"})
	if not check(not ui.bubble_labels.has("D"), "legacy notice comments do not produce a map bubble"): return
	print("PASS: ordered invitation and refusal bubbles, reconnection, timeline, history, matrix and layout")
	quit(0)
func expand_event(card: PanelContainer) -> String:
	var summary: Button = card.get_node("Body/Summary/Toggle")
	summary.button_pressed = true
	return card.get_node("Body/Summary/Inset/Fields").get_parsed_text() + card.get_node("Body/Details").get_parsed_text()

func dict_replay(event: Dictionary, id: int) -> Dictionary:
	var copy := event.duplicate(true)
	copy.id = id
	copy.replay = true
	return copy
