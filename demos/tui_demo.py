#!/usr/bin/env python3
"""Standalone, in-memory TUI prototype. No files, remotes, or user data are read."""

import argparse
import curses
import locale
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import List
from uuid import uuid4


KINDS = {"Project": "项目", "Task": "任务", "Update": "记录"}
BASE_VIEWS = ["收件箱", "当前", "等待", "项目", "归档", "回收站"]
MESH_VIEWS = ["Inbox", "任务", "项目", "报告"]


def width(text):
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def clipped(text, limit):
    if limit <= 0:
        return ""
    if width(text) <= limit:
        return text
    result = ""
    for c in text:
        if width(result + c) > limit - 1:
            break
        result += c
    return result + "…"


def wrapped(text, limit):
    result = []
    for paragraph in text.split("\n"):
        line = ""
        for c in paragraph:
            if width(line + c) > limit:
                if c in "，。！？；：、）》】」』" and line and limit >= 4:
                    result.append(line[:-1])
                    line = line[-1]
                else:
                    result.append(line)
                    line = ""
            line += c
        result.append(line)
    return result


@dataclass
class Document:
    id: str
    kind: str
    title: str
    project: str = "个人"
    state: str = "待推进"
    body: str = ""
    next_action: str = ""
    due: str = ""
    followup: str = ""
    waiting: str = ""
    inbox: bool = False
    lifecycle: str = "正常"
    previous_lifecycle: str = "正常"
    sources: List[str] = field(default_factory=list)
    converted_to: List[str] = field(default_factory=list)


def fixtures():
    return {
        "cosbase": [
            Document("P-001", "Project", "产品发布", state="进行中",
                     body="把发布材料、演示计划与重要日期放在一起。", next_action="先整理发布目标。"),
            Document("P-002", "Project", "示例项目", state="进行中",
                     body="个人阅读、分析准备与协作任务相关的私人安排。", next_action="检查使用场景的失败原因。"),
            Document("U-001", "Update", "发布计划：先讲用户问题", inbox=True, state="待整理",
                     body="随手记下的想法：先用一个明确的用户问题串起已有功能，再写下一阶段计划。"),
            Document("T-001", "Task", "完善发布计划", project="产品发布",
                     body="把问题、方法和预期成果串成一条清楚的主线。\n这是一条演示事项，日期和进展均为示例。",
                     next_action="先写三句话，说明核心用户问题。", due="2026-10-31"),
            Document("T-002", "Task", "检查三个使用场景", project="示例项目",
                     body="个人侧安排分析顺序。共享目标与共同进展保留在 CosMesh。",
                     next_action="对照分析笔记，列出三个区间的检查项。", followup="2026-10-28",
                     sources=["cosmesh:example-team/demo/T-M01"]),
            Document("T-003", "Task", "跟进合作者的反馈", project="示例项目", state="等待",
                     body="已整理问题，等待合作者反馈后再决定下一步。",
                     next_action="周五回看；若仍无反馈，再决定是否联系。",
                     followup="2026-10-30", waiting="合作者的意见"),
            Document("U-002", "Update", "上次讨论的摘要", state="已记录", lifecycle="已归档",
                     body="先核验功能边界，再讨论解释。原始记录归档后仍可查阅与恢复。"),
            Document("T-004", "Task", "重复的材料整理事项", lifecycle="回收站",
                     body="用于演示回收站恢复。恢复后保留原 ID。"),
        ],
        "cosmesh": [
            Document("U-M01", "Update", "反馈：优先核验场景边界", project="示例协作", state="待回应",
                     body="演示作者：Lead\n反馈要求先明确功能边界，再更新使用说明。\n此处只有示例内容，尚未认证或同步。",
                     next_action="读取来源任务，再由实际用户决定如何响应。", inbox=True,
                     sources=["T-M01"]),
            Document("T-M01", "Task", "完成 H I 功能验收", project="示例协作", state="in_progress",
                     body="演示 Lead：Alice\n演示 Member：Bob（已接受）\n整体任务状态由 Lead 明确确认。",
                     next_action="提交带操作说明的区间比较图。", due="2026-11-15",
                     sources=["U-M01"]),
            Document("T-M02", "Task", "复核演示页面", project="示例协作", state="review",
                     body="示例交付物已进入复核。成员的部分完成与整体任务完成分别记录。",
                     next_action="Lead 复核交付物和剩余问题。"),
            Document("P-M01", "Project", "示例协作", state="active",
                     body="研究目标、协作约定和成员目录。\n共享仓库读取范围由实际权限决定。"),
        ],
    }


class Demo:
    def __init__(self, product="cosbase"):
        self.product = product
        self.docs = fixtures()
        self.view_index = 1
        self.selected = 0
        self.focus = "list"
        self.query = ""
        self.message = "欢迎。按 ? 查看操作；所有内容均为示例，退出后重置。"

    @property
    def views(self):
        return BASE_VIEWS if self.product == "cosbase" else MESH_VIEWS

    @property
    def view(self):
        return self.views[self.view_index]

    def visible(self):
        docs = self.docs[self.product]
        view = self.view
        if self.product == "cosbase":
            if view in ("归档", "回收站"):
                docs = [d for d in docs if d.lifecycle == ("已归档" if view == "归档" else "回收站")]
            else:
                docs = [d for d in docs if d.lifecycle == "正常"]
                if view == "收件箱":
                    docs = [d for d in docs if d.inbox]
                elif view == "当前":
                    docs = [d for d in docs if d.kind == "Task" and d.state != "已结束"]
                elif view == "等待":
                    docs = [d for d in docs if d.waiting]
                elif view == "项目":
                    docs = [d for d in docs if d.kind == "Project"]
        else:
            kind = {"Inbox": "Update", "任务": "Task", "项目": "Project", "报告": None}[view]
            docs = [d for d in docs if d.kind == kind]
        if self.query:
            docs = [d for d in docs if self.query.casefold() in
                    (d.title + d.body + d.project).casefold()]
        self.selected = min(max(0, self.selected), max(0, len(docs) - 1))
        return docs

    def current(self):
        docs = self.visible()
        return docs[self.selected] if docs else None

    def switch(self, product):
        self.product = product
        self.view_index = 1
        self.selected = 0
        self.query = ""
        self.message = ("CosBase：个人文档可在内存中操作。" if product == "cosbase" else
                        "CosMesh：只读示例；身份、权限、同步和报告生成尚未接入。")

    def select_view(self, index):
        self.view_index = index % len(self.views)
        self.selected = 0

    def writable(self):
        if self.product == "cosmesh":
            self.message = "CosMesh 示例只读。演示未接入真实认证、写入或同步。"
            return False
        return True

    def create(self, kind, title, source=None):
        prefix = {"Project": "P", "Task": "T", "Update": "U"}[kind]
        doc = Document(prefix + "-" + uuid4().hex[:8], kind, title,
                       state="待整理" if kind == "Update" else "待推进",
                       inbox=kind == "Update", body="演示中新建的文档。退出后重置。")
        if source:
            doc.body = source.body
            doc.project = source.project
            doc.sources = [source.id]
        self.docs["cosbase"].append(doc)
        self.query = ""
        self.select_view({"Project": 3, "Task": 1, "Update": 0}[kind])
        self.selected = len(self.visible()) - 1
        self.focus = "list"
        return doc

    def convert(self, kind):
        source = self.current()
        if not self.writable() or not source:
            return
        if source.lifecycle != "正常":
            self.message = "请先恢复文档，再演示转换。"
            return
        target = self.create(kind, source.title, source)
        source.lifecycle = "已归档"
        source.converted_to.append(target.id)
        self.message = "已转换为%s；原文 %s 已归档，来源关联已保留。" % (KINDS[kind], source.id)

    def archive(self):
        if not self.writable():
            return
        doc = self.current()
        if not doc:
            return
        if doc.lifecycle == "回收站":
            self.message = "回收站文档请先按 r 恢复。"
            return
        doc.lifecycle = "正常" if doc.lifecycle == "已归档" else "已归档"
        self.message = "%s：%s。子文档保持原样。" % (doc.title, doc.lifecycle)

    def delete(self):
        if not self.writable():
            return
        doc = self.current()
        if not doc:
            return
        if doc.lifecycle == "回收站":
            self.message = "已在回收站中。此演示不提供彻底删除。"
            return
        doc.previous_lifecycle = doc.lifecycle
        doc.lifecycle = "回收站"
        self.message = "已移入回收站：%s。可在回收站按 r 恢复。" % doc.title

    def restore(self):
        if not self.writable():
            return
        doc = self.current()
        if not doc:
            return
        if doc.lifecycle == "正常":
            self.message = "当前文档已处于正常状态。"
            return
        doc.lifecycle = doc.previous_lifecycle if doc.lifecycle == "回收站" else "正常"
        self.message = "已恢复 %s，原 ID %s 保留。" % (doc.title, doc.id)


class Canvas:
    """A shared cell grid for curses rendering and plain-text snapshots."""
    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.cells = [[(" ", "normal") for _ in range(cols)] for _ in range(rows)]

    def put(self, y, x, text, style="normal", limit=None):
        if not 0 <= y < self.rows:
            return
        text = clipped(str(text), min(self.cols - x, limit if limit is not None else self.cols))
        for c in text:
            size = width(c)
            if size == 0:
                continue
            if x < 0 or x + size > self.cols:
                break
            self.cells[y][x] = (c, style)
            if size == 2:
                self.cells[y][x + 1] = ("", style)
            x += size

    def fill(self, y, x, length, style):
        self.put(y, x, " " * length, style)

    def text(self):
        return "\n".join("".join(c for c, _ in line).rstrip() for line in self.cells)


def render(demo, rows, cols):
    c = Canvas(rows, cols)
    if cols < 80 or rows < 24:
        c.put(1, 2, "CosMesh / CosBase · TUI DEMO", "accent")
        c.put(3, 2, "请将终端调整到至少 80 列 × 24 行。")
        c.put(5, 2, "q 退出；调整窗口大小后会自动重绘。")
        return c
    name = "CosBase" if demo.product == "cosbase" else "CosMesh"
    c.fill(0, 0, cols, "header")
    c.put(0, 2, name.upper() + "  /  " + ("PERSONAL" if demo.product == "cosbase" else "COLLABORATION"), "header")
    badge = "BETA UI DEMO · 示例数据"
    c.put(0, cols - width(badge) - 2, badge, "header")
    c.put(2, 2, "1 CosBase", "accent" if demo.product == "cosbase" else "muted")
    c.put(2, 16, "2 CosMesh", "accent" if demo.product == "cosmesh" else "muted")
    note = "仅内存 · 退出重置" if demo.product == "cosbase" else "只读示例 · 未连接服务器"
    c.put(2, cols - width(note) - 2, note, "muted")
    c.put(3, 0, "─" * cols, "muted")
    sidebar = 18
    divider = max(48, int(cols * .57))
    list_width = divider - sidebar - 4
    detail_x = divider + 3
    detail_width = cols - detail_x - 2
    for y in range(4, rows - 4):
        c.put(y, sidebar, "│", "muted")
        c.put(y, divider, "│", "muted")
    c.put(5, 2, "工作区", "section")
    for index, view in enumerate(demo.views):
        y = 7 + index * 2
        active = index == demo.view_index
        if active:
            c.fill(y, 1, sidebar - 2, "selected" if demo.focus == "nav" else "nav")
        c.put(y, 2, ("› " if active else "  ") + view,
              "selected" if active and demo.focus == "nav" else "accent" if active else "normal")
    c.put(rows - 7, 2, "DEMO · 10/27", "muted", sidebar - 3)
    docs = demo.visible()
    heading = "%s  ·  %s" % (demo.view, len(docs))
    c.put(5, sidebar + 2, heading, "section", list_width)
    if demo.query:
        c.put(6, sidebar + 2, "筛选：" + demo.query, "accent", list_width)
    capacity = max(1, (rows - 12) // 3)
    start = max(0, demo.selected - capacity + 1)
    for offset, doc in enumerate(docs[start:start + capacity]):
        index = start + offset
        y = 8 + offset * 3
        selected = index == demo.selected
        style = "selected" if selected and demo.focus == "list" else "normal"
        if style == "selected":
            c.fill(y, sidebar + 1, divider - sidebar - 1, style)
        c.put(y, sidebar + 2, ("› " if selected else "  ") + doc.title, style, list_width)
        meta = "%s · %s · %s" % (KINDS[doc.kind], doc.project, doc.state)
        c.put(y + 1, sidebar + 4, meta, "muted", list_width - 2)
    if not docs:
        c.put(8, sidebar + 2, "这里暂时没有文档。", "muted", list_width)
        hint = "报告生成尚未接入。" if demo.view == "报告" else "可切换视图或清除筛选。"
        c.put(10, sidebar + 2, hint, "muted", list_width)
    if len(docs) > capacity:
        c.put(rows - 6, sidebar + 2, "%s / %s  ·  ↑↓ 滚动" % (demo.selected + 1, len(docs)), "muted", list_width)
    doc = demo.current()
    if doc:
        c.put(5, detail_x, "文档预览", "section")
        lines = [(doc.title, "accent"),
                 ("%s · %s · %s" % (KINDS[doc.kind], doc.state, doc.lifecycle), "muted"),
                 (doc.id, "muted"), ("", "normal"), (doc.body, "normal")]
        if doc.next_action:
            lines += [("", "normal"), ("下一步", "section"), (doc.next_action, "normal")]
        for label, value in (("截止", doc.due), ("跟进", doc.followup), ("等待", doc.waiting)):
            if value:
                lines.append((label + "  " + value, "accent"))
        if doc.sources:
            lines += [("", "normal"), ("来源 / 关联", "section")]
            lines.extend((source, "muted") for source in doc.sources)
        if doc.converted_to:
            lines += [("转换去向", "section")]
            lines.extend((target, "muted") for target in doc.converted_to)
        y = 7
        flat = [(line, style) for text, style in lines for line in wrapped(text, detail_width)]
        remaining = rows - 5 - y
        for line, style in flat[:remaining]:
            c.put(y, detail_x, line, style, detail_width)
            y += 1
        if len(flat) > remaining:
            c.put(rows - 5, detail_x, "Enter 展开全文", "muted", detail_width)
    c.put(rows - 4, 0, "─" * cols, "muted")
    c.put(rows - 3, 2, demo.message, "accent", cols - 4)
    c.put(rows - 2, 2, "Tab 切换焦点  ↑↓/jk 选择  [ ] 换视图  Enter 展开  / 搜索", "muted", cols - 4)
    c.put(rows - 1, 2, "n 新建  t 转换  a 归档  d 回收  r 恢复  ? 帮助  q 退出", "muted", cols - 4)
    return c


def paint(screen, canvas, styles):
    screen.erase()
    for y, line in enumerate(canvas.cells):
        x = 0
        while x < canvas.cols:
            start, style, text = x, line[x][1], ""
            while x < canvas.cols and line[x][1] == style:
                text += line[x][0]
                x += 1
            try:
                screen.addstr(y, start, text, styles.get(style, 0))
            except curses.error:
                pass  # A terminal resize or writing the bottom-right cell may raise.
    screen.refresh()


def modal(screen, styles, title, options=None, prompt=False, text=None):
    """Small scrollable modal; get_wch preserves Chinese input."""
    selected, value, offset = 0, "", 0
    while True:
        rows, cols = screen.getmaxyx()
        c = Canvas(rows, cols)
        c.put(1, 3, title, "accent", cols - 6)
        c.put(2, 3, "─" * max(0, cols - 6), "muted")
        c.put(rows - 2, 3, "Esc 返回  ·  Enter 确认" if not text else "↑↓/jk 滚动  ·  Esc / Enter 返回", "muted", cols - 6)
        if prompt:
            c.put(5, 3, value + "▏", "normal", cols - 6)
        elif options:
            for i, option in enumerate(options):
                c.put(5 + i * 2, 3, ("› " if i == selected else "  ") + option,
                      "selected" if i == selected else "normal", cols - 6)
        elif text:
            lines = wrapped(text, max(1, cols - 6))
            offset = min(max(0, offset), max(0, len(lines) - max(1, rows - 7)))
            for y, line in enumerate(lines[offset:offset + max(1, rows - 7)], 4):
                c.put(y, 3, line)
        paint(screen, c, styles)
        key = screen.get_wch()
        if key == "\x1b":
            return None
        if key in ("\n", "\r", curses.KEY_ENTER):
            return value.strip() if prompt else selected if options else None
        if prompt:
            if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                value = value[:-1]
            elif isinstance(key, str) and key.isprintable() and len(value) < 120:
                value += key
        elif key in (curses.KEY_DOWN, "j"):
            if options:
                selected = (selected + 1) % len(options)
            else:
                offset += 1
        elif key in (curses.KEY_UP, "k"):
            if options:
                selected = (selected - 1) % len(options)
            else:
                offset -= 1


def run(screen, demo):
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    styles = {"normal": 0, "muted": curses.A_DIM, "accent": curses.A_BOLD,
              "section": curses.A_BOLD, "header": curses.A_REVERSE,
              "selected": curses.A_REVERSE, "nav": curses.A_BOLD}
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            for pair, foreground, background in ((1, curses.COLOR_CYAN, -1),
                                                  (2, curses.COLOR_BLACK, curses.COLOR_CYAN),
                                                  (3, curses.COLOR_WHITE, curses.COLOR_BLUE)):
                curses.init_pair(pair, foreground, background)
            styles.update(accent=curses.color_pair(1) | curses.A_BOLD,
                          selected=curses.color_pair(2) | curses.A_BOLD,
                          header=curses.color_pair(3) | curses.A_BOLD,
                          nav=curses.color_pair(1))
        except curses.error:
            pass
    while True:
        paint(screen, render(demo, *screen.getmaxyx()), styles)
        key = screen.get_wch()
        if key in ("q", "Q"):
            return
        if key in ("1", "2"):
            demo.switch("cosbase" if key == "1" else "cosmesh")
        elif key == "\t":
            demo.focus = "nav" if demo.focus == "list" else "list"
        elif key in ("[", "]"):
            demo.select_view(demo.view_index + (1 if key == "]" else -1))
        elif key in (curses.KEY_DOWN, "j", curses.KEY_UP, "k"):
            delta = 1 if key in (curses.KEY_DOWN, "j") else -1
            if demo.focus == "nav":
                demo.select_view(demo.view_index + delta)
            else:
                demo.selected += delta
        elif key in ("\n", "\r", curses.KEY_ENTER):
            doc = demo.current()
            if demo.focus == "nav":
                demo.focus = "list"
            elif doc:
                modal(screen, styles, doc.title,
                      text="%s · %s · %s\n%s\n\n%s\n\n下一步\n%s\n\n来源\n%s\n\n转换去向\n%s" %
                      (KINDS[doc.kind], doc.state, doc.lifecycle, doc.id, doc.body,
                       doc.next_action or "尚未填写", "\n".join(doc.sources) or "无",
                       "\n".join(doc.converted_to) or "无"))
        elif key == "/":
            query = modal(screen, styles, "搜索当前视图 · 输入标题、正文或项目", prompt=True)
            if query is not None:
                demo.query, demo.selected = query, 0
        elif key == "\x1b":
            demo.query = ""
        elif key == "n" and demo.writable():
            kinds = list(KINDS)
            choice = modal(screen, styles, "新建哪种文档？", options=[KINDS[k] for k in kinds])
            if choice is not None:
                title = modal(screen, styles, "文档标题 · 中文可直接输入或粘贴", prompt=True)
                if title:
                    doc = demo.create(kinds[choice], title)
                    demo.message = "已在内存中新建%s：%s" % (KINDS[doc.kind], doc.title)
        elif key == "t" and demo.writable() and demo.current():
            kinds = [kind for kind in KINDS if kind != demo.current().kind]
            choice = modal(screen, styles, "整份转换 · 新建目标，原文归档，保留来源", options=[KINDS[k] for k in kinds])
            if choice is not None:
                demo.convert(kinds[choice])
        elif key == "a":
            demo.archive()
        elif key == "d":
            demo.delete()
        elif key == "r":
            demo.restore()
        elif key == "?":
            modal(screen, styles, "CosMesh / CosBase · TUI 演示说明", text=(
                "1 / 2     切换 CosBase / CosMesh\nTab       切换侧栏与列表焦点\n"
                "↑↓ 或 j/k 选择；[ / ] 切换视图\nEnter     展开文档全文\n/         搜索当前视图；Esc 清除筛选\n"
                "n         新建 Project / Task / Update\nt         转换为另外两种类型之一，原文归档\n"
                "a         归档 / 取消归档\nd         移入回收站\nr         恢复归档或回收站中的文档\n"
                "q         退出\n\nCosBase 的文档操作仅在内存中生效，退出即重置。\n"
                "CosMesh 仅展示只读示例，未接入身份、权限、同步、真实图表或报告。\n"
                "日期、人物、进展与来源标识均为演示材料。此文件用于确认交互，尚未实现正式协议。"))


def main():
    parser = argparse.ArgumentParser(description="CosMesh / CosBase 内存 TUI 演示（标准库，无依赖）")
    parser.add_argument("--snapshot", action="store_true", help="输出纯文本界面快照，不要求交互终端")
    parser.add_argument("--product", choices=["cosbase", "cosmesh"], default="cosbase")
    parser.add_argument("--width", type=int, default=116, help="快照列数（80–240）")
    parser.add_argument("--height", type=int, default=32, help="快照行数（24–100）")
    args = parser.parse_args()
    demo = Demo(args.product)
    if args.snapshot:
        if not 80 <= args.width <= 240 or not 24 <= args.height <= 100:
            parser.error("快照尺寸范围：80–240 列，24–100 行")
        print(render(demo, args.height, args.width).text())
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("交互模式需要终端。可使用 --snapshot 查看纯文本演示。")
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(run, demo)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print("终端初始化失败：%s。可先用 --snapshot 查看界面。" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
