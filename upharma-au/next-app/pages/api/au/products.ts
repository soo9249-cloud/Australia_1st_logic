import type { NextApiRequest, NextApiResponse } from "next";

import { getSupabaseClient } from "@/lib/supabase";
import type { ProductSummary } from "@/lib/types";

type SuccessBody = {
  data: ProductSummary[];
  count: number;
};

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse<SuccessBody | { error: string }>,
): Promise<void> {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    res.status(405).json({ error: "Method not allowed" });
    return;
  }

  try {
    const supabase = getSupabaseClient();
    const { data, error, count } = await supabase
      .from("australia")
      .select("*", { count: "exact" })
      .order("crawled_at", { ascending: false });

    if (error) {
      console.error("[api/au/products]", error.message);
      res.status(500).json({ error: error.message });
      return;
    }

    const rows = (data ?? []) as ProductSummary[];
    res.status(200).json({
      data: rows,
      count: count ?? rows.length,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "알 수 없는 오류";
    console.error("[api/au/products]", msg);
    res.status(500).json({ error: msg });
  }
}
