import "./globals.css";

export const metadata = {
  title: "Next.js 热更新实验",
  description: "保留草稿和计数状态的真实 Next.js 开发页面。",
};

export default function RootLayout({ children }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
