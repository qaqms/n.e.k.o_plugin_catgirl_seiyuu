// 猫娘声优面板：模式控制 / 目标窗口与区域框选 / 规则设置 / 跳过记录 / 调试通道。
// Hosted TSX：只从 @neko/plugin-ui 导入；业务全在 Python 侧。
import {
  Alert,
  Button,
  Card,
  DataTable,
  Field,
  Grid,
  Input,
  NumberInput,
  Page,
  Select,
  Stack,
  StatCard,
  StatusBadge,
  Switch,
  Text,
  useCallback,
  useRef,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type SkipEntry = { text: string; reason: string }

type State = {
  mode?: string
  paused_reason?: string
  target?: { hwnd?: number; title?: string }
  focus?: boolean
  capture_supported?: boolean
  current_line?: string
  queue_size?: number
  spoken?: number
  last_error?: string
  last_mode_hint?: string
  region?: { x: number; y: number; w: number; h: number }
  settings?: {
    poll_interval_ms?: number
    stable_frames?: number
    dub_protagonist?: boolean
    dub_monologue?: boolean
    pause_on_user_message?: boolean
    slow_poll_when_unfocused?: boolean
    protagonist_names?: string[]
    ui_words?: string[]
    capture_mode?: string
  }
  target?: { hwnd?: number; title?: string; process?: string; minimized?: boolean }
  skipped?: SkipEntry[]
  ocr?: { available?: boolean; initialized?: boolean; error?: string; lang_type?: string; ocr_version?: string }
}

type WindowItem = {
  hwnd: number
  title: string
  label?: string
  process?: string
  pid: number
  minimized?: boolean
  focused?: boolean
  is_self?: boolean
}

const MODE_LABEL: Record<string, string> = {
  off: "未开启",
  running: "配音中",
  paused: "已暂停",
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, api } = props
  const toast = useToast()

  // ---- 目标窗口列表 ----
  const [windows, setWindows] = useState<WindowItem[]>([])
  const [selectedHwnd, setSelectedHwnd] = useState<string>("")
  const [windowFilter, setWindowFilter] = useState("")

  const refreshWindows = useCallback(async () => {
    try {
      const res = await api.call("dub_windows")
      setWindows((res?.windows || []) as WindowItem[])
    } catch (e) {
      toast.error(String(e))
    }
  }, [api, toast])

  // ---- 预览 + 区域框选 ----
  const [preview, setPreview] = useState<{ image_b64: string; width: number; height: number } | null>(null)
  const [drag, setDrag] = useState<null | { x0: number; y0: number; x1: number; y1: number }>(null)
  const [pending, setPending] = useState<null | { x: number; y: number; w: number; h: number }>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)

  const region = state.region || { x: 0.05, y: 0.68, w: 0.9, h: 0.24 }
  const shown = pending || region

  const grabPreview = useCallback(async () => {
    try {
      const hwnd = selectedHwnd ? Number(selectedHwnd) : 0
      const res = await api.call("capture_preview", { hwnd })
      setPreview({ image_b64: res.image_b64, width: res.width, height: res.height })
      setDrag(null)
      setPending(null)
    } catch (e) {
      toast.error(`截取失败：${e}`)
    }
  }, [api, selectedHwnd, toast])

  const relPoint = (ev: any) => {
    const el = imgRef.current
    if (!el) return { x: 0, y: 0 }
    const rect = el.getBoundingClientRect()
    const x = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width))
    const y = Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height))
    return { x, y }
  }

  const onMouseDown = (ev: any) => {
    const p = relPoint(ev)
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y })
  }
  const onMouseMove = (ev: any) => {
    if (!drag) return
    const p = relPoint(ev)
    setDrag({ ...drag, x1: p.x, y1: p.y })
  }
  const onMouseUp = () => {
    if (!drag) return
    const x = Math.min(drag.x0, drag.x1)
    const y = Math.min(drag.y0, drag.y1)
    const w = Math.abs(drag.x1 - drag.x0)
    const h = Math.abs(drag.y1 - drag.y0)
    if (w > 0.02 && h > 0.02) setPending({ x, y, w, h })
    setDrag(null)
  }

  const saveRegion = useCallback(async () => {
    const r = pending || region
    try {
      await api.call("set_region", { x: r.x, y: r.y, w: r.w, h: r.h })
      toast.success("区域已保存")
      setPending(null)
      await api.refresh()
    } catch (e) {
      toast.error(String(e))
    }
  }, [api, pending, region, toast])

  // ---- 设置表单 ----
  const s = state.settings || {}
  const [form, setForm] = useState({
    poll_interval_ms: 900,
    stable_frames: 2,
    dub_protagonist: false,
    dub_monologue: true,
    pause_on_user_message: true,
    slow_poll_when_unfocused: true,
    protagonist_names: "",
    ui_words: "",
    capture_mode: "auto",
  })
  const [formInit, setFormInit] = useState(false)
  if (!formInit && s.poll_interval_ms !== undefined) {
    setFormInit(true)
    setForm({
      poll_interval_ms: Number(s.poll_interval_ms || 900),
      stable_frames: Number(s.stable_frames || 2),
      dub_protagonist: Boolean(s.dub_protagonist),
      dub_monologue: s.dub_monologue !== false,
      pause_on_user_message: s.pause_on_user_message !== false,
      slow_poll_when_unfocused: s.slow_poll_when_unfocused !== false,
      protagonist_names: (s.protagonist_names || []).join(","),
      ui_words: (s.ui_words || []).join(","),
      capture_mode: String(s.capture_mode || "auto"),
    })
  }
  const patch = (key: string, value: any) => setForm({ ...form, [key]: value } as any)

  const saveSettings = useCallback(async () => {
    try {
      await api.call("update_settings", {
        patch: {
          poll_interval_ms: Number(form.poll_interval_ms),
          stable_frames: Number(form.stable_frames),
          dub_protagonist: Boolean(form.dub_protagonist),
          dub_monologue: Boolean(form.dub_monologue),
          pause_on_user_message: Boolean(form.pause_on_user_message),
          slow_poll_when_unfocused: Boolean(form.slow_poll_when_unfocused),
          protagonist_names: String(form.protagonist_names || "").split(/[,，]/).map((v) => v.trim()).filter(Boolean),
          ui_words: String(form.ui_words || "").split(/[,，]/).map((v) => v.trim()).filter(Boolean),
          capture_mode: String(form.capture_mode || "auto"),
        },
      })
      toast.success("设置已保存并生效")
      await api.refresh()
    } catch (e) {
      toast.error(String(e))
    }
  }, [api, form, toast])

  // ---- 手动投喂 / 调试 ----
  const [feedText, setFeedText] = useState("")
  const feed = useCallback(async () => {
    if (!feedText.trim()) return
    try {
      await api.call("feed_line", { line: feedText.trim() })
      setFeedText("")
      toast.success("已交给猫娘朗读")
    } catch (e) {
      toast.error(String(e))
    }
  }, [api, feedText, toast])

  const call = useCallback(async (entry: string, args?: Record<string, any>) => {
    try {
      await api.call(entry, args || {})
      await api.refresh()
    } catch (e) {
      toast.error(String(e))
    }
  }, [api, toast])

  const mode = state.mode || "off"
  // 全量窗口交给面板过滤：按标题/进程名/句柄搜索，前台窗口带 ★、最小化带 [min]。
  const filterKeys = windowFilter.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const filteredWindows = filterKeys.length
    ? windows.filter((w) => {
        const hay = `${w.title} ${w.process || ""} ${w.hwnd}`.toLowerCase()
        return filterKeys.every((k) => hay.includes(k))
      })
    : windows
  const windowOptions = filteredWindows.slice(0, 30).map((w) => ({
    label: `${w.focused ? "★ " : ""}${w.minimized ? "[min] " : ""}${w.label || w.title || String(w.hwnd)}${w.process ? ` — ${w.process}` : ""}`,
    value: String(w.hwnd),
  }))

  return (
    <Page title={props.plugin.name} subtitle={t("panel.subtitle")}>
      {(state.last_error && <Alert tone="warning">{state.last_error}</Alert>) || null}
      {(state.last_mode_hint && <Alert tone="info">{state.last_mode_hint}</Alert>) || null}

      <Card title={t("panel.mode")}>
        <Grid columns={4}>
          <StatCard label="状态" value={<StatusBadge text={`${MODE_LABEL[mode] || mode}${mode === "paused" && state.paused_reason ? `（${state.paused_reason}）` : ""}`} tone={mode === "running" ? "success" : mode === "paused" ? "warning" : "neutral"} />} />
          <StatCard label="已播句数" value={String(state.spoken ?? 0)} />
          <StatCard label="待播队列" value={String(state.queue_size ?? 0)} />
          <StatCard label="焦点" value={state.focus ? "前台" : mode === "running" ? "失焦降频" : "—"} />
        </Grid>
        <Text>{state.target?.title || state.target?.process ? `目标：${state.target.title || state.target.label || ""}${state.target.process ? `（${state.target.process}）` : ""}${state.target.minimized ? " · 已最小化" : ""}` : "未选择目标窗口（默认前台窗口）"}</Text>
        {(state.current_line && <Text>正在朗读：{state.current_line}</Text>) || null}
      </Card>

      <Card title={t("panel.controls")}>
        <Stack>
          <Grid columns={4}>
            <Button tone="primary" onClick={() => call("dub_start", { hwnd: selectedHwnd ? Number(selectedHwnd) : 0 })}>{t("actions.start")}</Button>
            <Button onClick={() => call("dub_pause")}>{t("actions.pause")}</Button>
            <Button onClick={() => call("dub_resume")}>{t("actions.resume")}</Button>
            <Button onClick={() => call("dub_stop")}>{t("actions.stop")}</Button>
          </Grid>
          <Field label={t("fields.line")}>
            <Input value={feedText} onChange={(v: any) => setFeedText(typeof v === "string" ? v : v?.target?.value || "")} placeholder="把这行台词交给猫娘…" />
          </Field>
          <Button onClick={feed}>{t("actions.feed")}</Button>
        </Stack>
      </Card>

      <Card title={t("panel.region")}>
        <Stack>
          {state.capture_supported === false && <Alert tone="warning">v0.1 截屏通道仅支持 Windows 桌面。</Alert>}
          <Grid columns={3}>
            <Select label="目标窗口" options={[{ label: "（跟随前台）", value: "" }, ...windowOptions]} value={selectedHwnd} onChange={(v: any) => setSelectedHwnd(typeof v === "string" ? v : v?.target?.value || "")} />
            <Button onClick={refreshWindows}>{t("actions.windows")}{windows.length ? `（${windows.length}）` : ""}</Button>
            <Button onClick={grabPreview}>{t("actions.preview")}</Button>
          </Grid>
          <Field label={t("panel.windowFilter")}>
            <Input value={windowFilter} onChange={(v: any) => setWindowFilter(typeof v === "string" ? v : v?.target?.value || "")} placeholder="过滤：标题 / 进程名 / 句柄（空格分隔多词）" />
          </Field>
          {preview ? (
            <div style={{ position: "relative", userSelect: "none" }}>
              <img
                ref={imgRef}
                src={`data:image/jpeg;base64,${preview.image_b64}`}
                style={{ width: "100%", display: "block", cursor: "crosshair" }}
                draggable={false}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={onMouseUp}
                alt="preview"
              />
              <div
                style={{
                  position: "absolute",
                  left: `${(drag ? Math.min(drag.x0, drag.x1) : shown.x) * 100}%`,
                  top: `${(drag ? Math.min(drag.y0, drag.y1) : shown.y) * 100}%`,
                  width: `${(drag ? Math.abs(drag.x1 - drag.x0) : shown.w) * 100}%`,
                  height: `${(drag ? Math.abs(drag.y1 - drag.y0) : shown.h) * 100}%`,
                  border: "2px solid #ff5f9e",
                  background: "rgba(255,95,158,0.15)",
                  pointerEvents: "none",
                }}
              />
            </div>
          ) : (
            <Text>先「截取预览」，再在画面上拖拽框住游戏的对话框区域。</Text>
          )}
          <Grid columns={2}>
            <Button tone="primary" onClick={saveRegion}>{t("actions.region")}</Button>
            <Button onClick={() => call("test_capture", { hwnd: selectedHwnd ? Number(selectedHwnd) : 0 })}>{t("actions.testOcr")}</Button>
          </Grid>
          <Text>区域 x={shown.x.toFixed(2)} y={shown.y.toFixed(2)} w={shown.w.toFixed(2)} h={shown.h.toFixed(2)}（相对窗口比例）</Text>
        </Stack>
      </Card>

      <Card title={t("panel.settings")}>
        <Stack>
          <Grid columns={2}>
            <NumberInput label={t("fields.pollInterval")} value={form.poll_interval_ms} min={200} max={10000} step={100} onChange={(v: any) => patch("poll_interval_ms", v)} />
            <NumberInput label={t("fields.stableFrames")} value={form.stable_frames} min={1} max={8} step={1} onChange={(v: any) => patch("stable_frames", v)} />
            <Switch label={t("fields.dubProtagonist")} checked={form.dub_protagonist} onChange={(v: any) => patch("dub_protagonist", v)} />
            <Switch label={t("fields.dubMonologue")} checked={form.dub_monologue} onChange={(v: any) => patch("dub_monologue", v)} />
            <Switch label={t("fields.pauseOnUser")} checked={form.pause_on_user_message} onChange={(v: any) => patch("pause_on_user_message", v)} />
            <Switch label={t("fields.slowPollUnfocused")} checked={form.slow_poll_when_unfocused} onChange={(v: any) => patch("slow_poll_when_unfocused", v)} />
            <Field label={t("fields.protagonistNames")}>
              <Input value={form.protagonist_names} onChange={(v: any) => patch("protagonist_names", typeof v === "string" ? v : v?.target?.value || "")} />
            </Field>
          </Grid>
            <Field label={t("fields.uiWords")}>
              <Input value={form.ui_words} onChange={(v: any) => patch("ui_words", typeof v === "string" ? v : v?.target?.value || "")} />
            </Field>
            <Field label={t("fields.captureMode")}>
              <Select options={[
                { label: t("capture.auto"), value: "auto" },
                { label: t("capture.window"), value: "window" },
                { label: t("capture.screen"), value: "screen" },
              ]} value={form.capture_mode} onChange={(v: any) => patch("capture_mode", typeof v === "string" ? v : v?.target?.value || "auto")} />
            </Field>
          <Button tone="primary" onClick={saveSettings}>{t("actions.settings")}</Button>
        </Stack>
      </Card>

      <Card title={t("panel.skipped")}>
        <DataTable
          data={state.skipped || []}
          rowKey="text"
          columns={[
            { key: "text", label: "文本" },
            { key: "reason", label: "原因" },
          ]}
        />
      </Card>

      <Card title={t("panel.debug")}>
        <Stack>
          <Text>OCR：{state.ocr?.initialized ? `已加载（${state.ocr?.lang_type}/${state.ocr?.ocr_version}）` : state.ocr?.error ? `未就绪：${state.ocr.error}` : "惰性加载中（首次识别时）"}</Text>
          <Grid columns={2}>
            <Button onClick={() => call("test_speak", { line: "配音通道测试，一。配音通道测试，二。" })}>{t("actions.testSpeak")}</Button>
            <Button onClick={() => call("ocr_download")}>{t("actions.ocrModels")}</Button>
          </Grid>
          <Text>{t("panel.ocrHint")}</Text>
        </Stack>
      </Card>
    </Page>
  )
}
