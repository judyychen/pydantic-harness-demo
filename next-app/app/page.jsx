"use client";

import TeaStore from "../components/TeaStore";

import { useEffect, useRef, useState } from "react";

export default function Home() {
  const title = "把春天，留给一杯茶";
  const accent = "#234d3b";
  const step = 3;
  const revision = "visual-b-v7";
  const [count, setCount] = useState(0);
  const [draft, setDraft] = useState("");
  const mountId = useRef(null);
  const root = useRef(null);
  const proof = useRef(null);

  useEffect(() => {
    mountId.current ??= crypto.randomUUID();
    window.__NEXT_SPIKE__ = {
      document,
      documentId: window.__NEXT_SPIKE__?.documentId ?? crypto.randomUUID(),
      timeOrigin: performance.timeOrigin,
      mountId: mountId.current,
      count,
      draft,
      step,
      title,
      accent,
      revision,
      renders: (window.__NEXT_SPIKE__?.renders ?? 0) + 1,
      committedAt: performance.now(),
      experiment: new URLSearchParams(location.search).get("experiment"),
      origin: location.origin,
    };
    const { document: currentDocument, ...snapshot } = window.__NEXT_SPIKE__;
    for (const key of ["documentId", "timeOrigin", "mountId", "count", "draft", "step", "revision", "renders"]) {
      root.current.dataset[key] = String(snapshot[key]);
    }
    proof.current.textContent = JSON.stringify(snapshot, null, 2);
  });

  return (
    <main ref={root} style={{ "--accent": accent }} data-revision={revision}>
      <TeaStore />
      <details className="experiment"><summary>实时开发实验 · 草稿与计数器</summary>
      <p className="eyebrow">真实 Next.js 页面</p>
      <h1 id="page-title">{title}</h1>
      <p className="intro">先输入一段草稿并增加计数，再让 Agent 修改标题、颜色或增加步长。</p>

      <section aria-labelledby="draft-heading">
        <h2 id="draft-heading">你的草稿</h2>
        <label htmlFor="draft">随便写几句话</label>
        <input id="draft" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="这段输入应该在热更新后保留" />
        <p className="draft-preview">当前草稿：<output id="draft-value">{draft || "（还没有输入）"}</output></p>
      </section>

      <section aria-labelledby="counter-heading">
        <h2 id="counter-heading">计数器</h2>
        <p>当前计数：<output id="count" aria-live="polite">{count}</output></p>
        <button id="increment" onClick={() => setCount((value) => value + step)}>增加 {step}</button>
        <button id="reset" className="secondary" onClick={() => setCount(0)}>清零</button>
      </section>

      <p className="hint">页面更新时，输入和计数应该保留。当前版本：<span id="revision">{revision}</span></p>
      <details className="proof">
        <summary>查看本页验收信息</summary>
        <pre id="proof-values" ref={proof}>等待浏览器加载…</pre>
      </details>
      </details>
    </main>
  );
}
