import { Router, type IRouter } from "express";
import extractRouter from "./extract";

const router: IRouter = Router();

// Example's health.ts is deliberately not copied: it imports
// @workspace/api-zod, which is part of Example's OpenAPI codegen chain and
// not part of step 2. Only the extract route is in scope here.
router.use(extractRouter);

export default router;
