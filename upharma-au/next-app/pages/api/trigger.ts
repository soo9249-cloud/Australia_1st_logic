// GitHub Actions workflow_dispatch API 트리거(구현 예정).

import type { NextApiRequest, NextApiResponse } from 'next';

export default function handler(_req: NextApiRequest, res: NextApiResponse): void {
  res.status(501).end();
}
