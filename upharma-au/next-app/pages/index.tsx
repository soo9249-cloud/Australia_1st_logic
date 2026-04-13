import Link from "next/link";

export default function Home(): JSX.Element {
  return (
    <div style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <p>
        <Link href="/au">호주 1공정 시장조사 (/au)</Link>로 이동하세요.
      </p>
    </div>
  );
}
