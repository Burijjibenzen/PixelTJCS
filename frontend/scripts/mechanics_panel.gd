extends CanvasLayer
## 交付演示中的机制说明，可在游戏内直接核对规则和当前状态。

var window: PanelContainer
var status: Label

func _ready() -> void:
	layer = 22
	var button := Button.new()
	button.name = "Open"
	button.theme = preload("res://assets/tea_theme.tres")
	button.text = "机制说明"
	button.custom_minimum_size = Vector2(110, 32)
	button.pressed.connect(func():
		window.visible = not window.visible
		if window.visible:
			_refresh())
	add_child(button)
	var place_button := func(): button.position = get_viewport().get_visible_rect().size - Vector2(122, 44)
	get_viewport().size_changed.connect(place_button)
	place_button.call()
	window = PanelContainer.new()
	window.theme = preload("res://assets/tea_theme.tres")
	window.position = Vector2(280, 86)
	window.custom_minimum_size = Vector2(720, 560)
	window.mouse_force_pass_scroll_events = false
	var style := StyleBoxFlat.new()
	style.bg_color = Color("#f5f0e7")
	style.border_width_top = 3
	style.border_color = Color("#ae9978")
	style.set_corner_radius_all(10)
	style.set_content_margin_all(18)
	window.add_theme_stylebox_override("panel", style)
	add_child(window)
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 12)
	window.add_child(body)
	var heading := HBoxContainer.new()
	body.add_child(heading)
	var title := Label.new()
	title.text = "PixelTJCS · 核心机制"
	title.add_theme_font_size_override("font_size", 22)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(title)
	var close := Button.new()
	close.text = "X"
	close.tooltip_text = "关闭"
	close.pressed.connect(func() -> void: window.hide())
	heading.add_child(close)
	var drag := preload("res://scripts/window_drag.gd").new()
	add_child(drag)
	drag.bind(window, title)
	status = Label.new()
	body.add_child(status)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(scroll)
	var explanation := RichTextLabel.new()
	explanation.bbcode_enabled = true
	explanation.fit_content = true
	explanation.custom_minimum_size.x = 670
	explanation.text = """
[b]1. 世界时间与动态日程[/b]
正常速度下，现实 1 秒约等于游戏 10 分钟。每名角色保留基础作息，也可依据本人记忆、公开事件和活动计划改变目的地。地图上的实际位置与目的地分开；角色走到后才算到达。后端离线时前端显示本地演示日程。

[b]2. 记忆、遗忘和反思[/b]
角色根据性格、好感、已有记忆和洞察形成第一人称回忆。NPC 在同一房间或走廊、距说话者 150 地图像素内可以听到台词并形成旁听印象。记忆强度 = 0.6 × 时间新近度 + 0.4 × 重要性，新近度每 300 游戏分钟减半；强度低于 0.30 会归入遗忘档案。每名角色最多保留 60 条活跃记忆；新增记忆达到阈值时可做反思压缩。社交观察面板可查看记忆变化。

[b]3. 社交关系与八卦[/b]
相遇和对话由后端调度，角色只依据本人已知信息回应。真实对话完成后才调整关系，并保存变化原因。八卦需要实际来源和传播路径，最多经过 3 跳；知道一件事的角色与仅看见气泡的观众并不相同。

[b]4. 公告栏[/b]
发布是公开事件；角色走近并阅读后才形成个人记忆。阅读触发新的自主动作时，角色可能自言自语。

[b]5. 玩家模式[/b]
玩家默认不在世界中。加入后可在主地图行走，看到「按E交互」时可与白边标出的角色交谈或查看告示栏。靠近楼梯时箭头会高亮，按 E 换层。按 P 把镜头拉回自己。交谈记录、双方记忆和关系原因会保存，离开只关闭当前会话。

[b]6. 上帝模式[/b]
可以发布公告，也可以用悄悄话指派一个角色前往地点、执行活动、原地停留或找人交谈。重置世界恢复第 1 天和初始位置，并清空本轮存档。
"""
	scroll.add_child(explanation)
	window.hide()

func _refresh() -> void:
	status.text = "当前：%s · %s" % ["后端驱动" if ApiClient.backend_online else "本地演示", ApiClient.backend_time]

func _unhandled_key_input(event: InputEvent) -> void:
	if window.visible and event.is_action_pressed("ui_cancel"):
		window.hide()
		get_viewport().set_input_as_handled()
