# RunPod posture

## Default decision

**OFF for Prophet Hacks.**

The tick loop is CPU and I/O bound. Forecasting is dispatched to a frontier
API (Opus 4.7) over HTTPS. Adding GPU infrastructure introduces more
failure modes than it removes: a misbehaving container, a hung volume
mount, a flaky pod, or network egress quirks all become live-event risks.

The event clock is short. Debugging GPU or networking issues mid-sprint
trades against the actual work (forecasting quality, calibration,
agreement-gate logic). The cost-benefit fails.

## Scenarios where RunPod earns its keep

Each scenario has a trigger threshold, a recommended RunPod config, an
estimated cost, and a clear decision point. If a scenario triggers, the
decision to spin up RunPod is Rob's, not an agent's.

---

### Scenario A: OSS model hosting for cost optimization

Run Llama-3.3-70B-Instruct or DeepSeek-V3.1 via vLLM as a third
independent forecast. Useful for the multi-model agreement gate without
paying frontier-tier cost per call.

- **Trigger threshold:** estimated 10-day eval-window spend on
  Anthropic + OpenAI exceeds $200, OR frontier rate limits start blocking
  ticks (multiple 429s per hour after backoff).
- **RunPod config:** 1x H100 SXM (80GB) on a community pod; vLLM server
  with `--gpu-memory-utilization 0.92`, `--max-model-len 16384`, OpenAI-
  compatible API on `:8000`.
- **Estimated cost:** community H100 ~$2.40/h. 24h continuous ~$58.
  Vendor-list 70B-instruct call cost via providers like Together is
  comparable for low volume; RunPod wins past ~2M output tokens/day.
- **Decision point:** end of hour 6. If frontier spend on hours 0-6
  annualizes above the trigger, spin up RunPod for the remaining window.

### Scenario B: Embedding service for retrieval

If retrieval lands and we want to embed candidate evidence cheaply, a
RunPod-hosted embedding endpoint (BGE-large, E5-large, or similar) is
much cheaper than per-token API calls at any non-trivial volume.

- **Trigger threshold:** `policy.retrieval_enabled = true` lands AND we
  embed at least 50 docs per tick (so 50 * (ticks per hour) per hour).
- **RunPod config:** 1x L4 or A10 (24GB) pod running a FastAPI wrapper
  around `sentence-transformers`. Stateless.
- **Estimated cost:** L4 community ~$0.70/h. 24h ~$17. Compares against
  per-token API embedding cost at ~$0.02 per 1M tokens for small models;
  break-even at moderate volume, RunPod wins decisively at high volume.
- **Decision point:** when retrieval is first wired (probably hour 16+).
  Default to API embeddings until volume justifies the pod.

### Scenario C: Fine-tuning a forecasting head

Only if Prophet Arena exposes historical resolution data and we have
6+ hours of slack mid-sprint with the rest of the agent stable.

- **Trigger threshold:** historical labels accessible AND base agent
  has been running clean for at least 4 hours AND we have 6+ hours of
  event clock left.
- **RunPod config:** 1x A100 80GB pod for LoRA fine-tuning a 7B-class
  base model on event-resolution pairs. Save adapter weights to S3 or
  the RunPod network volume.
- **Estimated cost:** A100 community ~$1.80/h. 6h ~$11 plus storage.
- **Decision point:** rare. For a first-time submission this is over the
  risk budget. Document the approach for next sprint instead.

### Scenario D: Specialized OSS forecasting models

If a HuggingFace model specifically tuned for prediction markets or
event forecasting surfaces during the event, RunPod is the cleanest way
to host it.

- **Trigger threshold:** a candidate model exists, is small enough to
  fit on a single L4 or A10, and has a published Brier improvement over
  generic instruct models on a comparable benchmark.
- **RunPod config:** matched to the model size. L4 (24GB) for anything
  up to ~13B quantized; A100 (80GB) for 70B-class.
- **Estimated cost:** L4 ~$0.70/h, A100 ~$1.80/h.
- **Decision point:** if the candidate model is identified during scoping
  (hour 0-2), evaluate it as part of the variant lineup. Otherwise hold.

---

## Default reiterated

Off unless a scenario above is triggered. Triggering one is a Rob
decision, not an agent decision. If you (an agent) believe a scenario
is triggered, update `docs/AGENT_STATUS.md` with the trigger evidence
and escalate. Do not spin up infrastructure on your own.
