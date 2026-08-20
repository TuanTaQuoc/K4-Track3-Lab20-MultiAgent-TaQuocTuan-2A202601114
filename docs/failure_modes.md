# Failure Modes và cách khắc phục

**Học viên:** Tạ Quốc Tuấn — **MSSV:** 2A202601114

Tài liệu này ghi lại các failure mode quan sát được khi xây dựng hệ multi-agent trong lab,
kèm cách đã fix trong code.

---

## 1. Vòng lặp vô hạn giữa Supervisor và worker

**Triệu chứng.** Supervisor route sang một worker, worker thất bại im lặng và không ghi gì
vào state, supervisor thấy field vẫn rỗng nên route lại đúng worker đó. Vòng lặp chạy mãi
và đốt token cho tới khi bị kill.

**Nguyên nhân gốc.** Routing dựa trên *"field nào còn rỗng"* là điều kiện về **kết quả**,
không phải về **nỗ lực đã bỏ ra**. Nếu worker không bao giờ điền được field, điều kiện
không bao giờ đổi.

**Cách fix — hai tầng chặn độc lập:**

- Tầng nghiệp vụ, trong `SupervisorAgent.decide()`
  ([supervisor.py](../src/multi_agent_research_lab/agents/supervisor.py)):
  `iteration >= max_iterations` → `done`, cộng thêm `len(errors) >= 3` → `done` để dừng sớm
  khi lỗi lặp lại thay vì đợi hết quota iteration.
- Tầng hạ tầng, trong `MultiAgentWorkflow.run()`:
  `recursion_limit = max_iterations * 2 + 5` truyền vào `compiled.invoke()`.

Tầng thứ hai tồn tại vì tầng thứ nhất là *code do tôi viết và có thể có bug*. Nếu
`decide()` sai logic, LangGraph vẫn cắt được vòng lặp.

**Cách kiểm chứng.** `test_max_iterations_guardrail_stops_the_loop` và
`test_repeated_errors_trigger_early_stop` trong
[tests/test_supervisor_routing.py](../tests/test_supervisor_routing.py).

---

## 2. Trả về `None` khi hết iteration — lỗi lan sang tầng trên

**Triệu chứng.** Khi guardrail cắt vòng lặp, `final_answer` vẫn là `None`. CLI in ra rỗng,
benchmark chia cho `None`, và người dùng không biết chuyện gì đã xảy ra.

**Nguyên nhân gốc.** Guardrail chỉ *dừng* mà không *giải thích*. Dừng an toàn nhưng im lặng
thì vẫn là một failure mode.

**Cách fix.** Trong `SupervisorAgent.run()`, khi route ra `done` mà `final_answer` còn rỗng,
supervisor tự ghi một câu trả lời fallback nêu rõ lý do dừng (số iteration đã dùng,
`max_iterations`, số lỗi) và append lý do đó vào `state.errors`. Hệ thống degrade có kiểm
soát thay vì trả về khoảng trống.

---

## 3. Search provider chết làm sập cả workflow

**Triệu chứng.** Tavily rate-limit hoặc mất mạng → exception ném từ `SearchClient.search()`
→ Researcher chết → toàn bộ run hỏng, không demo được.

**Cách fix — fallback có đánh dấu, không fallback im lặng:**

`SearchClient` bắt exception và trả về `_mock_sources()`. Nhưng điểm quan trọng là nguồn
mock được gắn `metadata={"provider": "mock", "verified": False}`, và snippet của nó ghi thẳng
rằng *"Mọi kết luận rút ra từ đây phải được đánh dấu là chưa kiểm chứng"*.

Analyst đếm số nguồn mock và đưa con số đó vào prompt; Critic đếm số nguồn mock **được trích
dẫn** và đưa vào findings. Nghĩa là chất lượng suy giảm được **truyền xuống** chứ không bị
giấu đi — người đọc report biết câu trả lời dựa trên nguồn giả.

Đây là điểm tôi cho là quan trọng nhất: một fallback giấu việc mình đang chạy ở chế độ suy
giảm còn nguy hiểm hơn là để nó fail thẳng.

---

## 4. Hallucinated citation — trích dẫn nguồn không tồn tại

**Triệu chứng.** Writer sinh ra `[S7]` trong khi Researcher chỉ thu được 5 nguồn. Câu trả lời
trông rất đáng tin vì có citation, nhưng citation trỏ vào hư không.

**Tại sao prompt không đủ để chặn.** Đã yêu cầu trong system prompt của Writer là *"chỉ dùng
thông tin từ notes"*, nhưng prompt là ràng buộc mềm — model vẫn có thể vi phạm.

**Cách fix — kiểm tra deterministic, không dùng LLM:**

`CriticAgent` ([critic.py](../src/multi_agent_research_lab/agents/critic.py)) dùng regex
`\[S(\d+)\]` trích mọi citation index, so với `set(range(1, len(sources)+1))`:

- `cited - valid` → **dangling citation**, đây là bằng chứng hallucination, ghi vào `errors`.
- `valid - cited` → nguồn thu thập nhưng không dùng (tín hiệu search kém hiệu quả).

Tôi cố ý **không** dùng LLM làm critic ở đây. Một LLM-judge có thể tự nó hallucinate khi
đánh giá; còn so khớp index là phép kiểm tra xác định, luôn cho cùng kết quả, và chi phí
bằng 0.

**Giới hạn đã biết.** Critic chỉ bắt được lỗi *cấu trúc* citation. Nó không phát hiện được
trường hợp `[S2]` tồn tại nhưng nội dung câu văn không thật sự nằm trong nguồn S2. Muốn bắt
loại lỗi đó cần entailment check giữa từng câu và snippet nguồn — nằm ngoài phạm vi lab.

---

## 5. Writer che giấu điểm yếu để câu trả lời "trông ngon hơn"

**Triệu chứng.** Analyst flag rõ ràng rằng một luận điểm thiếu dữ liệu định lượng, nhưng
Writer bỏ qua và viết như thể mọi thứ đã được chứng minh.

**Nguyên nhân gốc.** Mục tiêu ngầm "viết cho thuyết phục" xung đột với mục tiêu "trung thực".
Trong single-agent baseline, hai mục tiêu này nằm trong cùng một prompt nên mục tiêu thuyết
phục thường thắng.

**Cách fix — biến sự trung thực thành đầu ra bắt buộc:**

- Analyst có một mục **bắt buộc** tên *Weak evidence*, và prompt ghi rõ:
  *"Đây là mục quan trọng nhất — phải trung thực, không được bỏ trống cho đẹp."*
- Writer bị yêu cầu: nếu Analyst đã flag weak evidence thì **phải** có mục `## Limitations`,
  kèm câu *"Không được giấu điểm yếu để câu trả lời trông thuyết phục hơn thực tế."*
- `heuristic_quality_score()` **cộng điểm** cho câu trả lời có nêu limitation, thay vì
  chỉ thưởng cho độ dài và độ trau chuốt.

Trong run thực tế, câu trả lời multi-agent có mục `## Limitations` chỉ ra 4 claim thiếu dữ
liệu định lượng — đúng loại nội dung mà baseline không tạo ra.

---

## 6. Tracing hỏng làm chết workflow

**Triệu chứng.** Sai Langfuse key hoặc mất mạng → exception từ tầng tracing → workflow chết,
dù bản thân logic nghiệp vụ hoàn toàn ổn.

**Nguyên nhân gốc.** Observability là hệ thống *phụ trợ*. Để nó nằm trên đường đi chính của
request là lỗi thiết kế: công cụ quan sát lỗi lại trở thành nguồn gây lỗi.

**Cách fix.** Mọi thao tác Langfuse trong
[tracing.py](../src/multi_agent_research_lab/observability/tracing.py) — khởi tạo client, mở
span, đóng span, `flush()` — đều bọc `try/except` và chỉ ghi `logger.debug`. Khi thiếu key,
`_get_langfuse()` trả `None` ngay và `trace_span` degrade về bộ đếm thời gian local. Workflow
không bao giờ biết tracing đã hỏng.

---

## Tổng kết

Nguyên tắc chung rút ra từ 6 failure mode trên:

| Nguyên tắc | Áp dụng ở đâu |
|---|---|
| Guardrail phải có **hai tầng độc lập** | max_iterations (nghiệp vụ) + recursion_limit (hạ tầng) |
| Dừng an toàn phải **giải thích được**, không được im lặng | fallback answer của supervisor |
| Degrade phải **truyền xuống**, không được giấu | metadata `verified: False` của nguồn mock |
| Ràng buộc quan trọng cần **kiểm tra xác định**, không chỉ dựa vào prompt | Critic so khớp index citation |
| Hệ phụ trợ **không được** nằm trên đường đi chính | tracing bọc try/except toàn bộ |
