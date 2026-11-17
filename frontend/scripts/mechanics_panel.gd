extends CanvasLayer
## 交付演示中的机制说明，可在游戏内直接核对规则和当前状态。

var window: PanelContainer
var status: Label

func _ready() -> void:
	layer = 22
	var button := Button.new()
	button.text = "机制说明"
	button.position = Vector2(1010, 12)
	button.custom_minimum_size = Vector2(110, 32)
	button.pressed.connect(func() -> void: window.show(); _refresh())
	add_child(button)
	window = PanelContainer.new()
	window.position = Vector2(280, 70)
	window.custom_minimum_size = Vector2(720, 560)
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.055, 0.085, 0.13, 0.98)
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
	title.text = "Roamchestra · 核心机制"
	title.add_theme_font_size_override("font_size", 22)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(title)
	var close := Button.new()
	close.text = "关闭"
	close.pressed.connect(func() -> void: window.hide())
	heading.add_child(close)
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
对话、公告阅读等经历先写入各自的第一人称记忆；旁观不会自动让所有角色知情。记忆强度 = 0.6 × 时间新近度 + 0.4 × 重要性，新近度每 300 游戏分钟减半；强度低于 0.30 会归入遗忘档案。每名角色最多保留 60 条活跃记忆；新增记忆达到阈值时可做反思压缩。社交观察面板可查看记忆变化。

[b]3. 社交关系与八卦[/b]
相遇和对话由后端调度，角色只依据本人已知信息回应。真实对话完成后才调整关系，并保存变化原因。八卦需要实际来源和传播路径，最多经过 3 跳；知道一件事的角色与仅看见气泡的观众并不相同。

[b]4. 公告栏[/b]
发布是公开事件；角色需要走近并阅读，才形成个人记忆并可能评论。公告内容不会凭空写进全部角色记忆。

[b]5. 玩家模式[/b]
玩家默认不在世界中。加入后可在主地图行走，靠近角色按 E 交谈；靠近楼梯按 E 换楼层，按 P 把镜头拉回自己。交谈记录、双方记忆和关系原因会保存；离开只关闭当前会话。
"""
	scroll.add_child(explanation)
	window.hide()

func _refresh() -> void:
	status.text = "当前：%s · %s" % ["后端驱动" if ApiClient.backend_online else "本地演示", ApiClient.backend_time]

func _unhandled_key_input(event: InputEvent) -> void:
	if window.visible and event.is_action_pressed("ui_cancel"):
		window.hide()
		get_viewport().set_input_as_handled()
