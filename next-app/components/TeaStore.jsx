"use client";
import { useEffect, useState } from "react";
import TeaArt from "./TeaArt";
const products = [
  { id: 1, name: "春日绿茶", type: "绿茶", price: 128, note: "一盏新绿，唤醒春日", taste: "清鲜 · 豆香 · 回甘" },
  { id: 2, name: "山岚乌龙", type: "乌龙茶", price: 168, note: "山风入盏，花香悠长", taste: "花香 · 醇和 · 悠长" },
  { id: 3, name: "暖山红茶", type: "红茶", price: 138, note: "温润茶汤，慢享日常", taste: "甜香 · 温润 · 柔和" },
  { id: 4, name: "月下白茶", type: "白茶", price: 158, note: "清简一杯，自在片刻", taste: "清雅 · 柔甜 · 淡香" },
];
const money = (n) => `¥${n.toFixed(2)}`;
export default function TeaStore() {
  const [filter, setFilter] = useState("全部");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [grams, setGrams] = useState(50);
  const [cart, setCart] = useState([]);
  const [panel, setPanel] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const [message, setMessage] = useState("");
  const [feedbackId, setFeedbackId] = useState(0);
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(""), 4000);
    return () => clearTimeout(timer);
  }, [message, feedbackId]);
  function notify(text) { setMessage(text); setFeedbackId((id) => id + 1); }
  const quantity = cart.reduce((sum, i) => sum + i.qty, 0);
  const total = cart.reduce((sum, i) => sum + i.price * i.qty, 0);
  const shown = products.filter((p) => (filter === "全部" || p.type === filter) && p.name.includes(search.trim()));
  function add(p, size = 50) {
    const key = `${p.id}-${size}`;
    setCart((items) => items.some((i) => i.key === key) ? items.map((i) => i.key === key ? { ...i, qty: i.qty + 1 } : i) : [...items, { ...p, key, grams: size, price: p.price * size / 50, qty: 1 }]);
    notify(`${p.name} · ${size}g 已加入茶篮`);
  }
  function change(key, delta) { setCart((items) => items.map((i) => i.key === key ? { ...i, qty: i.qty + delta } : i).filter((i) => i.qty > 0)); }
  return <div className="store">
    <div className="announcement">春日茶叙 · 把日子泡成喜欢的模样 <span>演示商城 / 非真实交易</span></div>
    <header className="store-header"><a className="brand" href="#home">山间茶事<small>SHAN JIAN TEA</small></a><nav aria-label="商城导航"><a href="#home">首页</a><a href="#collection">寻一味茶</a><a href="#story">山间来信</a></nav><button className="basket-button" onClick={() => { setPanel("cart"); setReceipt(null); }}>茶篮 <span className="basket-count" key={quantity}>{quantity}</span></button></header>
    <section className="tea-hero" id="home"><div><p className="eyebrow">春茶上新 · SPRING TEA</p><h1>山间一抹新绿</h1><p className="hero-sub">春入茶盏，清香自来。</p><p className="muted">一杯春茶，一段慢下来的时光。<br />以清新茶意，开启日常里的片刻悠然。</p><a className="primary-link" href="#collection">寻一味春茶 <span>↗</span></a><div className="hero-tags">春日茶叙　 /　 清新茶意　 /　 慢享时光</div></div><div className="hero-art"><span className="vertical">一叶 · 一盏 · 一时光</span><TeaArt /><span className="art-caption">山间好茶 / 日常好时光</span></div></section>
    <div className="values"><span>01 / 以茶会友</span><span>02 / 慢享日常</span><span>03 / 自在茶事</span></div>
    <section id="collection" className="collection"><div className="section-heading"><div><p className="eyebrow">THE TEA COLLECTION</p><h2>寻一味，心头好</h2></div><p className="muted">各有茶性，各得欢喜。</p></div><div className="catalog-tools"><div className="filters">{["全部", "绿茶", "乌龙茶", "红茶", "白茶"].map((type) => <button key={type} aria-pressed={filter === type} onClick={() => setFilter(type)}>{type}</button>)}</div><input aria-label="搜索茶叶" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索一味茶…" /></div><p className="demo-note">商品、规格、价格及风味描述均为演示数据，非真实在售商品。</p>
    <p className="result-summary" role="status">{filter === "全部" ? "全部茶品" : filter} · 共 {shown.length} 款{search.trim() && ` · 搜索「${search.trim()}」`}</p><div className="product-grid">{shown.map((p) => <article className="product" key={p.id}><button className={`product-art tone-${p.id}`} aria-label={`查看${p.name}详情`} onClick={() => { setSelected(p); setGrams(50); }}><span className="product-number">0{p.id} / {p.type}</span><span className="tea-tin"><span>山间茶事</span><strong>{p.name}</strong><small>SHAN JIAN TEA<br />50g · 演示</small></span><span className="leaf-decoration">❧</span></button><div className="product-info"><span>{p.type} / {p.taste}</span><h3><button onClick={() => { setSelected(p); setGrams(50); }}>{p.name}</button></h3><p>{p.note}</p><div className="product-bottom"><strong>{money(p.price)} <small>/ 50g</small></strong><button className={cart.some((i) => i.id === p.id) ? "in-cart" : ""} onClick={() => add(p)}>{cart.some((i) => i.id === p.id) ? "✓ 再添一份" : "＋ 入茶篮"}</button></div></div></article>)}</div>{shown.length === 0 && <p className="empty">暂未找到这味茶，试试其他名称或分类。</p>}</section>
    <section className="story" id="story"><div className="story-mark">茶<span>人与草木之间</span></div><div><p className="eyebrow">A MOMENT OF QUIET</p><h2>不赶时间，<br />只赴一场茶约。</h2><p>我们想把关于茶的日常，过得简单一些。<br />让一杯清茶，陪你读几页书、听一场雨，<br />也陪你找回属于自己的节奏。</p><small>山间茶事 · 品牌故事演示</small></div></section>
    <footer className="store-footer"><div className="brand">山间茶事<small>一叶知春，一盏安心。</small></div><p>商城交互演示，无真实支付、订单或配送。<br />购物车与模拟订单仅在当前页面有效，刷新后不保留。</p><a href="#home">回到顶部 ↑</a></footer><div className="cart-notice" role="status" aria-live="polite">{message && <div className="toast-content" key={feedbackId}><span className="success-icon" aria-hidden="true">✓</span><span>{message}</span><button onClick={() => { setPanel("cart"); setReceipt(null); setMessage(""); }}>查看茶篮 →</button><button aria-label="关闭提示" onClick={() => setMessage("")}>×</button></div>}</div>
    {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><section className="modal" role="dialog" aria-modal="true" aria-label="商品详情" onClick={(e) => e.stopPropagation()}><button autoFocus className="close" aria-label="关闭详情" onClick={() => setSelected(null)}>×</button><p className="eyebrow">山间茶事 / {selected.type}</p><h2>{selected.name}</h2><p>{selected.note}</p><p className="muted">{selected.taste} · 风味描述仅供演示</p><fieldset><legend>选择规格</legend>{[50, 100].map((g) => <button key={g} aria-pressed={grams === g} onClick={() => setGrams(g)}>{g}g</button>)}</fieldset><p className="detail-price">{money(selected.price * grams / 50)}</p><p>冲泡建议（演示）：取适量茶叶，按个人口味调整水温与浸泡时间。</p><button onClick={() => { add(selected, grams); setSelected(null); }}>加入茶篮</button></section></div>}
    {panel && <div className="modal-backdrop" onClick={() => setPanel(null)}><section className="modal" role="dialog" aria-modal="true" aria-label="茶篮与模拟结算" onClick={(e) => e.stopPropagation()}><button autoFocus className="close" aria-label="关闭茶篮" onClick={() => setPanel(null)}>×</button><p className="eyebrow">YOUR TEA BASKET</p><h2>{receipt ? "茶约已记下" : panel === "checkout" ? "确认这份茶意" : "我的茶篮"}</h2>{receipt ? <div className="receipt-success" role="status"><span className="receipt-check" aria-hidden="true">✓</span><p>模拟订单已生成，共 {receipt.quantity} 件，合计 {money(receipt.total)}。</p><p>未扣款、不会配送，也没有提交个人信息。</p><button onClick={() => setPanel(null)}>继续逛逛</button></div> : cart.length === 0 ? <p className="empty">茶篮还空着，去寻一味喜欢的茶吧。</p> : <><div className="cart-items">{cart.map((i) => <div className="cart-row" key={i.key}><div><strong>{i.name}</strong><small>{i.grams}g · {money(i.price)} / 件</small></div><div className="quantity"><button aria-label={`减少${i.name}${i.grams}g`} onClick={() => change(i.key, -1)}>−</button><span className="quantity-value" key={i.qty}>{i.qty}</span><button aria-label={`增加${i.name}${i.grams}g`} onClick={() => change(i.key, 1)}>＋</button></div><button className="remove" onClick={() => setCart((items) => items.filter((item) => item.key !== i.key))}>移除</button></div>)}</div><p className="total">合计 <strong className="total-value" key={total}>{money(total)}</strong></p><p className="demo-note">模拟结算无运费、无真实付款，不采集收货信息。</p>{panel === "checkout" ? <><button onClick={() => { setReceipt({ total, quantity }); setCart([]); }}>确认生成模拟订单</button><button className="secondary" onClick={() => setPanel("cart")}>返回茶篮</button></> : <button onClick={() => setPanel("checkout")}>前往模拟结算 →</button>}</>}</section></div>}
  </div>;
}
