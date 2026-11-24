extends Node

var _browser_window: JavaScriptObject
var _paste_key_callback: JavaScriptObject
var _paste_callback: JavaScriptObject

func _ready() -> void:
	if not OS.has_feature("web"):
		return
	_browser_window = JavaScriptBridge.get_interface("window")
	_paste_key_callback = JavaScriptBridge.create_callback(_on_browser_paste_key)
	_paste_callback = JavaScriptBridge.create_callback(_on_browser_paste)
	_browser_window.addEventListener("keydown", _paste_key_callback, true)
	_browser_window.addEventListener("paste", _paste_callback, true)

func _exit_tree() -> void:
	if _browser_window != null:
		_browser_window.removeEventListener("keydown", _paste_key_callback, true)
		_browser_window.removeEventListener("paste", _paste_callback, true)

func _focused_editor() -> Control:
	var viewport := get_viewport()
	if viewport.is_input_disabled():
		return null
	var editor := viewport.gui_get_focus_owner()
	if (editor is TextEdit or editor is LineEdit) and editor.editable and editor.is_visible_in_tree():
		return editor
	return null

func _on_browser_paste_key(args: Array) -> void:
	var event: JavaScriptObject = args[0]
	if (event.ctrlKey or event.metaKey) and not event.altKey and str(event.key).to_lower() == "v" and _focused_editor() != null:
		# Godot 的画布会取消按键默认动作，先让浏览器产生 paste 事件。
		event.stopPropagation()

func _on_browser_paste(args: Array) -> void:
	if _focused_editor() == null:
		return
	var event: JavaScriptObject = args[0]
	var clipboard: JavaScriptObject = event.clipboardData
	if clipboard != null and paste_text(str(clipboard.getData("text/plain"))):
		event.preventDefault()
		event.stopImmediatePropagation()

func paste_text(text: String) -> bool:
	var editor := _focused_editor()
	if editor == null or text.is_empty():
		return false
	var normalized := text.replace("\r\n", "\n").replace("\r", "\n")
	if editor is TextEdit:
		editor.insert_text_at_caret(normalized)
	else:
		var line := editor as LineEdit
		var had_selection := line.has_selection()
		var before := line.text
		if had_selection:
			var start := line.get_selection_from_column()
			line.delete_text(start, line.get_selection_to_column())
			line.deselect()
			line.caret_column = start
		line.insert_text_at_caret(normalized.replace("\n", " "))
		# 删除选区会延迟发出 text_changed。纯插入需要主动通知表单。
		if not had_selection and line.text != before:
			line.text_changed.emit(line.text)
	return true
