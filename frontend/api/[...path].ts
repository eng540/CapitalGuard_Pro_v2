import type { Request, Response } from "express";
import { createCapitalGuardApp } from "../server/_core/index";

const app = createCapitalGuardApp();

export default function handler(req: Request, res: Response) {
  return app(req, res);
}
