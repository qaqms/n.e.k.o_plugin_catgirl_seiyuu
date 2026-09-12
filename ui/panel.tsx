// 猫娘声优面板：模式控制 / 目标窗口与区域框选 / 规则设置 / 跳过记录 / 调试通道。
// Hosted TSX：只从 @neko/plugin-ui 导入；业务全在 Python 侧。
//
// i18n 契约（与 forever_companion 主项 #10 同源）：
// - 用户可见文案一律走 t(key, { defaultValue })，zh-CN bundle 与 defaultValue
//   逐字同源（tests/test_i18n_contract.py 常驻门钉住）；
// - 面板可达入口的 Err 消息只接受稳定 ASCII 码（^[a-z][a-z0-9_]*$），由
//   errorText() 按 panel.errors.<camelCase(码)> 翻译；非码文本（宿主自身错误、
//   超时等）原样直出；
// - 给模型的指令（llm_tool note / HUD 文案）在 Python 侧，不走本文件。
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

const ERROR_CODE_RE = /^[a-z][a-z0-9_]*$/

function codeToCamel(code: string): string {
  return code.replace(/_+([a-z0-9])/g, (_m, c: string) => c.toUpperCase())
}

function errorText(raw: unknown, t: (key: string, opts?: Record<string, any>) => string): string {
  const msg = raw instanceof Error ? raw.message : String(raw == null ? "" : raw)
  if (ERROR_CODE_RE.test(msg)) {
    // defaultValue 给码本身：新码忘了进 bundle 时退化为英文码（可排查），
    // 而不是把某一语言的裸串直喷给所有用户。
    return t(`panel.errors.${codeToCamel(msg)}`, { defaultValue: msg })
  }
  return msg
}

type SkipEntry = { text: string; reason: string }

type State = {
  mode?: string
  paused_reason?: string
  target?: { hwnd?: number; title?: string; process?: string; minimized?: boolean }
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

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, api: surfaceApi } = props
  const toast = useToast()

  // ---- 目标窗口列表 ----
  const [windows, setWindows] = useState<WindowItem[]>([])
  const [selectedHwnd, setSelectedHwnd] = useState<string>("")
  const [windowFilter, setWindowFilter] = useState("")

  const refreshWindows = useCallback(async () => {
    try {
      const res = await surfaceApi.call("dub_windows")
      setWindows((res?.windows || []) as WindowItem[])
    } catch (e) {
      toast.error(errorText(e, t))
    }
  }, [surfaceApi, t, toast])

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
      const res = await surfaceApi.call("capture_preview", { hwnd })
      setPreview({ image_b64: res.image_b64, width: res.width, height: res.height })
      setDrag(null)
      setPending(null)
    } catch (e) {
      toast.error(t("panel.toast.grabFailed", { defaultValue: "截取失败：{error}", error: errorText(e, t) }))
    }
  }, [surfaceApi, selectedHwnd, t, toast])

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
      await surfaceApi.call("set_region", { x: r.x, y: r.y, w: r.w, h: r.h })
      toast.success(t("panel.toast.regionSaved", { defaultValue: "区域已保存" }))
      setPending(null)
      await surfaceApi.refresh()
    } catch (e) {
      toast.error(errorText(e, t))
    }
  }, [surfaceApi, pending, region, t, toast])

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
      await surfaceApi.call("update_settings", {
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
      toast.success(t("panel.toast.settingsSaved", { defaultValue: "设置已保存并生效" }))
      await surfaceApi.refresh()
    } catch (e) {
      toast.error(errorText(e, t))
    }
  }, [surfaceApi, form, t, toast])

  // ---- 手动投喂 / 调试 ----
  const [feedText, setFeedText] = useState("")
  const feed = useCallback(async () => {
    if (!feedText.trim()) return
    try {
      await surfaceApi.call("feed_line", { line: feedText.trim() })
      setFeedText("")
      toast.success(t("panel.toast.fed", { defaultValue: "已交给猫娘朗读" }))
    } catch (e) {
      toast.error(errorText(e, t))
    }
  }, [surfaceApi, feedText, t, toast])

  const call = useCallback(async (entry: string, args?: Record<string, any>) => {
    try {
      await surfaceApi.call(entry, args || {})
      await surfaceApi.refresh()
    } catch (e) {
      toast.error(errorText(e, t))
    }
  }, [surfaceApi, t, toast])

  const mode = state.mode || "off"
  const modeLabel = mode === "running"
    ? t("panel.modeLabel.running", { defaultValue: "配音中" })
    : mode === "paused"
      ? t("panel.modeLabel.paused", { defaultValue: "已暂停" })
      : t("panel.modeLabel.off", { defaultValue: "未开启" })
  const pauseReason = state.paused_reason === "user"
    ? t("panel.pauseReason.user", { defaultValue: "主人发言" })
    : state.paused_reason === "manual"
      ? t("panel.pauseReason.manual", { defaultValue: "手动" })
      : state.paused_reason === "conflict"
        ? t("panel.pauseReason.conflict", { defaultValue: "语音通道被占用" })
        : state.paused_reason === "error"
          ? t("panel.pauseReason.error", { defaultValue: "异常" })
          : state.paused_reason || ""

  // 全量窗口交给面板过滤：按标题/进程名/句柄搜索，前台窗口带 ★、最小化带 [min]。
  const filterKeys = windowFilter.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const filteredWindows = filterKeys.length
    ? windows.filter((w) => {
        const hay = `${w.title} ${w.process || ""} ${w.hwnd}`.toLowerCase()
        return filterKeys.every((k) => hay.includes(k))
      })
    : windows
  const windowOptions = filteredWindows.slice(0, 30).map((w) => ({
    label: `${w.focused ? "★ " : ""}${w.minimized ? "[min] " : ""}${w.label || w.title || String(w.hwnd)}${w.process ? ` - ${w.process}` : ""}`,
    value: String(w.hwnd),
  }))

  const tgt = state.target
  const ocr = state.ocr

  return (
    <Page title={props.plugin.name} subtitle={t("panel.subtitle", { defaultValue: "让猫娘用她本人的声音逐字朗读游戏台词。" })}>
      {(state.last_error && <Alert tone="warning">{errorText(state.last_error, t)}</Alert>) || null}
      {(state.last_mode_hint && <Alert tone="info">{t("panel.hint.lastMode", { defaultValue: "上次退出时处于配音模式；为不打扰主人，本次未自动恢复。" })}</Alert>) || null}

      <Card title={t("panel.mode", { defaultValue: "模式" })}>
        <Grid cols={4}>
          <StatCard label={t("panel.stat.status", { defaultValue: "状态" })} value={<StatusBadge label={`${modeLabel}${mode === "paused" && pauseReason ? `（${pauseReason}）` : ""}`} tone={mode === "running" ? "success" : mode === "paused" ? "warning" : "default"} />} />
          <StatCard label={t("panel.stat.lines", { defaultValue: "已播句数" })} value={String(state.spoken ?? 0)} />
          <StatCard label={t("panel.stat.queue", { defaultValue: "待播队列" })} value={String(state.queue_size ?? 0)} />
          <StatCard label={t("panel.stat.focus", { defaultValue: "焦点" })} value={state.focus ? t("panel.stat.foreground", { defaultValue: "前台" }) : mode === "running" ? t("panel.stat.slowPoll", { defaultValue: "失焦降频" }) : "-"} />
        </Grid>
        <Text>
          {tgt?.title || tgt?.process
            ? t("panel.target.current", {
                defaultValue: "目标：{name}{process}{minimized}",
                name: tgt.title || "",
                process: tgt.process ? t("panel.target.process", { defaultValue: "（{process}）", process: tgt.process }) : "",
                minimized: tgt.minimized ? t("panel.target.minimized", { defaultValue: " · 已最小化" }) : "",
              })
            : t("panel.target.none", { defaultValue: "未选择目标窗口（默认跟随前台）" })}
        </Text>
        {(state.current_line && <Text>{t("panel.speaking", { defaultValue: "正在朗读：{line}", line: state.current_line })}</Text>) || null}
      </Card>

      <Card title={t("panel.controls", { defaultValue: "控制" })}>
        <Stack>
          <Grid cols={4}>
            <Button tone="primary" onClick={() => call("dub_start", { hwnd: selectedHwnd ? Number(selectedHwnd) : 0 })}>{t("actions.start", { defaultValue: "开始配音" })}</Button>
            <Button onClick={() => call("dub_pause")}>{t("actions.pause", { defaultValue: "暂停" })}</Button>
            <Button onClick={() => call("dub_resume")}>{t("actions.resume", { defaultValue: "继续" })}</Button>
            <Button onClick={() => call("dub_stop")}>{t("actions.stop", { defaultValue: "停止配音" })}</Button>
          </Grid>
          <Field label={t("fields.line", { defaultValue: "台词文本" })}>
            <Input value={feedText} onChange={(v: any) => setFeedText(typeof v === "string" ? v : v?.target?.value || "")} placeholder={t("panel.feed.placeholder", { defaultValue: "把这行台词交给猫娘…" })} />
          </Field>
          <Button onClick={feed}>{t("actions.feed", { defaultValue: "朗读这一句" })}</Button>
        </Stack>
      </Card>

      <Card title={t("panel.region", { defaultValue: "对话框区域框选" })}>
        <Stack>
          {state.capture_supported === false && <Alert tone="warning">{t("panel.captureUnsupported", { defaultValue: "截屏通道仅支持 Windows 桌面。" })}</Alert>}
          <Grid cols={3}>
            <Field label={t("panel.target", { defaultValue: "目标窗口" })}>
              <Select options={[{ label: t("panel.window.followFg", { defaultValue: "（跟随前台）" }), value: "" }, ...windowOptions]} value={selectedHwnd} onChange={(v: any) => setSelectedHwnd(typeof v === "string" ? v : v?.target?.value || "")} />
            </Field>
            <Button onClick={refreshWindows}>{t("actions.windows", { defaultValue: "刷新窗口列表" })}{windows.length ? `（${windows.length}）` : ""}</Button>
            <Button onClick={grabPreview}>{t("actions.preview", { defaultValue: "截取预览" })}</Button>
          </Grid>
          <Field label={t("panel.windowFilter", { defaultValue: "窗口过滤" })}>
            <Input value={windowFilter} onChange={(v: any) => setWindowFilter(typeof v === "string" ? v : v?.target?.value || "")} placeholder={t("panel.filter.placeholder", { defaultValue: "过滤：标题 / 进程名 / 句柄（空格分隔多词）" })} />
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
            <Text>{t("panel.region.hint", { defaultValue: "先「截取预览」，再在画面上拖拽框住游戏的对话框区域。" })}</Text>
          )}
          <Grid cols={2}>
            <Button tone="primary" onClick={saveRegion}>{t("actions.region", { defaultValue: "保存区域" })}</Button>
            <Button onClick={() => call("test_capture", { hwnd: selectedHwnd ? Number(selectedHwnd) : 0 })}>{t("actions.testOcr", { defaultValue: "测试识别" })}</Button>
          </Grid>
          <Text>{t("panel.region.line", { defaultValue: "区域 x={x} y={y} w={w} h={h}（相对窗口比例）", x: shown.x.toFixed(2), y: shown.y.toFixed(2), w: shown.w.toFixed(2), h: shown.h.toFixed(2) })}</Text>
        </Stack>
      </Card>

      <Card title={t("panel.settings", { defaultValue: "配音规则" })}>
        <Stack>
          <Grid cols={2}>
            <Field label={t("fields.pollInterval", { defaultValue: "轮询间隔（毫秒）" })}>
              <NumberInput value={form.poll_interval_ms} min={200} max={10000} step={100} onChange={(v: any) => patch("poll_interval_ms", v)} />
            </Field>
            <Field label={t("fields.stableFrames", { defaultValue: "稳定帧数" })}>
              <NumberInput value={form.stable_frames} min={1} max={8} step={1} onChange={(v: any) => patch("stable_frames", v)} />
            </Field>
            <Switch label={t("fields.dubProtagonist", { defaultValue: "朗读主角台词" })} checked={form.dub_protagonist} onChange={(v: any) => patch("dub_protagonist", v)} />
            <Switch label={t("fields.dubMonologue", { defaultValue: "朗读内心独白" })} checked={form.dub_monologue} onChange={(v: any) => patch("dub_monologue", v)} />
            <Switch label={t("fields.pauseOnUser", { defaultValue: "主人说话时自动暂停" })} checked={form.pause_on_user_message} onChange={(v: any) => patch("pause_on_user_message", v)} />
            <Switch label={t("fields.slowPollUnfocused", { defaultValue: "失焦降频轮询" })} checked={form.slow_poll_when_unfocused} onChange={(v: any) => patch("slow_poll_when_unfocused", v)} />
            <Field label={t("fields.protagonistNames", { defaultValue: "主角名（逗号分隔）" })}>
              <Input value={form.protagonist_names} onChange={(v: any) => patch("protagonist_names", typeof v === "string" ? v : v?.target?.value || "")} />
            </Field>
            <Field label={t("fields.uiWords", { defaultValue: "UI 屏蔽词（逗号分隔）" })}>
              <Input value={form.ui_words} onChange={(v: any) => patch("ui_words", typeof v === "string" ? v : v?.target?.value || "")} />
            </Field>
            <Field label={t("fields.captureMode", { defaultValue: "截屏方式" })}>
              <Select options={[
                { label: t("capture.auto", { defaultValue: "自动（窗口优先，失败回退桌面）" }), value: "auto" },
                { label: t("capture.window", { defaultValue: "仅窗口本体（抗遮挡，个别游戏不支持）" }), value: "window" },
                { label: t("capture.screen", { defaultValue: "仅桌面截屏（旧行为）" }), value: "screen" },
              ]} value={form.capture_mode} onChange={(v: any) => patch("capture_mode", typeof v === "string" ? v : v?.target?.value || "auto")} />
            </Field>
          </Grid>
          <Button tone="primary" onClick={saveSettings}>{t("actions.settings", { defaultValue: "保存设置" })}</Button>
        </Stack>
      </Card>

      <Card title={t("panel.skipped", { defaultValue: "跳过的行（供纠错）" })}>
        <DataTable
          data={state.skipped || []}
          rowKey="text"
          columns={[
            { key: "text", label: t("panel.skipCol.text", { defaultValue: "文本" }) },
            { key: "reason", label: t("panel.skipCol.reason", { defaultValue: "原因" }) },
          ]}
        />
      </Card>

      <Card title={t("panel.debug", { defaultValue: "调试" })}>
        <Stack>
          <Text>
            {ocr?.initialized
              ? t("panel.ocr.loaded", { defaultValue: "OCR：已加载（{model}）", model: `${ocr.lang_type}/${ocr.ocr_version}` })
              : ocr?.error
                ? t("panel.ocr.error", { defaultValue: "OCR：未就绪：{error}", error: ocr.error })
                : t("panel.ocr.lazy", { defaultValue: "OCR：惰性加载中（首次识别时）" })}
          </Text>
          <Grid cols={2}>
            {/* 测试句是给宿主 TTS 的语料（数据），不是界面文案 → 保持中文原样 */}
            <Button onClick={() => call("test_speak", { line: "配音通道测试，一。配音通道测试，二。" })}>{t("actions.testSpeak", { defaultValue: "试听播报" })}</Button>
            <Button onClick={() => call("ocr_download")}>{t("actions.ocrModels", { defaultValue: "下载其它语言模型" })}</Button>
          </Grid>
          <Text>{t("panel.ocrHint", { defaultValue: "默认中文 v4 模型已随包内置，无需下载；仅当切换 [ocr] 语言/版本时才需要。" })}</Text>
        </Stack>
      </Card>
    </Page>
  )
}
