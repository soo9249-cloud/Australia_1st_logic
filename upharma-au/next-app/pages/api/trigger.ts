import type { NextApiRequest, NextApiResponse } from "next";

type Body = {
  product_id?: string;
};

type Ok = {
  message: string;
  product_id: string;
};

type Err = {
  error: string;
};

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse<Ok | Err>,
): Promise<void> {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    res.status(405).json({ error: "Method not allowed" });
    return;
  }

  const raw = req.body as Body | undefined;
  const product_id =
    typeof raw?.product_id === "string" ? raw.product_id.trim() : "";

  if (!product_id) {
    res.status(400).json({ error: "product_id 가 필요합니다." });
    return;
  }

  const token = process.env.GH_TOKEN;
  const owner = process.env.GITHUB_OWNER;
  const repo = process.env.GITHUB_REPO;

  if (!token || !owner || !repo) {
    res.status(500).json({
      error: "GH_TOKEN, GITHUB_OWNER, GITHUB_REPO 환경변수를 확인하세요.",
    });
    return;
  }

  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/au_crawl.yml/dispatches`;

  try {
    const gh = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { product_filter: product_id },
      }),
    });

    if (!gh.ok) {
      const text = await gh.text();
      res.status(500).json({
        error: text || `GitHub API 오류: ${gh.status} ${gh.statusText}`,
      });
      return;
    }

    res.status(200).json({ message: "크롤링 시작됨", product_id });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    res.status(500).json({ error: msg });
  }
}
