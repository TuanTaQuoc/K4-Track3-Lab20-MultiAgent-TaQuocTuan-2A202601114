# Lab Guide: Multi-Agent Research System

## Scenario

Bạn cần xây dựng một research assistant có thể nhận câu hỏi dài, tìm thông tin, phân tích và viết câu trả lời cuối cùng. Lab yêu cầu so sánh hai cách làm:

1. **Single-agent baseline**: một agent làm toàn bộ.
2. **Multi-agent workflow**: Supervisor điều phối Researcher, Analyst, Writer.

## Quy tắc quan trọng

- Không thêm agent nếu không có lý do rõ ràng.
- Mỗi agent phải có responsibility riêng.
- Shared state phải đủ rõ để debug.
- Phải có trace hoặc log cho từng bước.
- Phải benchmark, không chỉ nhìn output bằng cảm tính.

## Milestone 1: Baseline

File gợi ý:

- `src/multi_agent_research_lab/cli.py`
- `src/multi_agent_research_lab/services/llm_client.py`

TODO(student): thay baseline placeholder bằng một call LLM thật.

## Milestone 2: Supervisor

File gợi ý:

- `src/multi_agent_research_lab/agents/supervisor.py`
- `src/multi_agent_research_lab/graph/workflow.py`

TODO(student): implement routing policy.

Gợi ý câu hỏi thiết kế:

- Khi nào gọi Researcher?
- Khi nào gọi Analyst?
- Khi nào gọi Writer?
- Khi nào stop?
- Nếu agent fail thì retry hay fallback?

## Milestone 3: Worker agents

File gợi ý:

- `src/multi_agent_research_lab/agents/researcher.py`
- `src/multi_agent_research_lab/agents/analyst.py`
- `src/multi_agent_research_lab/agents/writer.py`

TODO(student): implement từng worker.

## Milestone 4: Trace và benchmark

File gợi ý:

- `src/multi_agent_research_lab/observability/tracing.py`
- `src/multi_agent_research_lab/evaluation/benchmark.py`
- `src/multi_agent_research_lab/evaluation/report.py`

Benchmark tối thiểu:

| Metric | Cách đo gợi ý |
|---|---|
| Latency | wall-clock time |
| Cost | token usage hoặc provider usage |
| Quality | rubric 0-10 do peer review |
| Citation coverage | số claims có source / tổng claims chính |
| Failure rate | số query fail / tổng query |

## Troubleshooting

### macOS: lỗi SSL certificate khi gọi API qua HTTPS (Tavily, OpenAI, ...)

Triệu chứng: khi implement `SearchClient` (hoặc bất kỳ HTTPS call nào) trên macOS, bạn có thể gặp lỗi kiểu:

```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
unable to get local issuer certificate
```

Nguyên nhân: Python cài từ python.org trên macOS **không dùng** certificate store của hệ điều hành, nên không tìm thấy CA bundle hợp lệ. Đây là lỗi môi trường, **không phải** do API key sai.

Cách khắc phục (chọn 1 trong 3):

1. **Chạy script cài certificate đi kèm Python** (nhanh nhất):

   ```bash
   /Applications/Python\ 3.12/Install\ Certificates.command
   ```

   (thay `3.12` bằng version Python của bạn)

2. **Dùng `certifi` trong code** — thêm `certifi` vào dependencies, rồi tạo SSL context khi gọi HTTPS:

   ```python
   import certifi
   import ssl
   from urllib.request import urlopen

   ssl_context = ssl.create_default_context(cafile=certifi.where())
   urlopen(request, timeout=timeout, context=ssl_context)
   ```

3. **Set biến môi trường** trỏ tới CA bundle của certifi (không cần đổi code):

   ```bash
   export SSL_CERT_FILE=$(python -m certifi)
   ```

## Exit ticket

Mỗi nhóm trả lời 2 câu:

1. Case nào nên dùng multi-agent? Vì sao?
2. Case nào không nên dùng multi-agent? Vì sao?

---

### Trả lời — Tạ Quốc Tuấn (MSSV 2A202601114)

Các con số dưới đây lấy từ [`reports/benchmark_report.md`](../reports/benchmark_report.md),
chạy trên query `"Research GraphRAG state-of-the-art and write a 500-word summary"`.

#### 1. Case nào nên dùng multi-agent?

**a. Khi các bước cần năng lực khác nhau, không chỉ chất lượng khác nhau.**

Researcher cần search tool; Analyst và Writer thì không. Baseline chạy một prompt không có
search nên `sources=0` và **không dẫn được nguồn nào**; multi-agent lấy 5 nguồn thật,
citation coverage 100%. Khác biệt ở đây không phải "viết hay hơn" mà là *làm được việc mà
bên kia không làm được* — prompt engineering giỏi đến mấy cũng không tạo ra nguồn cho baseline.

**b. Khi trong một prompt có hai mục tiêu xung đột.**

"Viết cho thuyết phục" đấu với "trung thực về điểm yếu"; trong một prompt, mục tiêu thuyết
phục thường thắng. Tách Analyst thành agent riêng — cấm viết kết luận, bắt buộc điền mục
*Weak evidence* — biến sự trung thực thành output bắt buộc. Thực tế: câu trả lời multi-agent
có mục `## Limitations` chỉ ra 4 claim thiếu dữ liệu định lượng, baseline không có.

> Cần nói rõ: bằng chứng cho ý này **yếu**. `heuristic_quality_score()` cộng điểm cho chữ
> "limitation" trong khi Writer bị prompt *bắt buộc* viết mục đó — vòng tròn logic. Cái quan
> sát được chỉ là khác biệt định tính, n=1.

**c. Khi cần cắm kiểm tra xác định vào giữa các bước.**

`CriticAgent` so khớp index citation với `len(sources)` bằng regex để bắt citation trỏ tới
nguồn không tồn tại — chi phí $0, không tự hallucinate như LLM-judge. Chỉ làm được vì có ranh
giới rõ giữa "nguồn đã thu" và "câu trả lời đã viết"; trong single prompt không có chỗ nào để
cắm kiểm tra đó vào.

**d. Khi cần định vị lỗi.**

Trace `researcher_done → analyst_done → writer_done → critic PASS` cho biết sai ở chặng nào.
Baseline là hộp đen: sai thì chỉ biết "câu trả lời sai", không biết vì tìm sai hay viết sai.

#### 2. Case nào không nên dùng multi-agent?

**a. Task một bước, một năng lực.** Dịch, tóm tắt văn bản đã có sẵn, phân loại, trích xuất
field. Không có gì để handoff; supervisor chỉ thêm một LLM call để trả lời câu hỏi mà một
câu `if` đã trả lời được.

**b. Khi latency là ràng buộc cứng.** 28.08s so với 19.31s. Với chatbot realtime thì 28s là
hỏng sản phẩm. Tệ hơn: 4 call **tuần tự** vì mỗi bước cần output của bước trước, nên không
rút ngắn được bằng chạy song song.

**c. Khi chi phí nhân lên mà giá trị không nhân lên.** Cost cao hơn **3.44x**, với input token
phình từ **118 → 3845** do notes bị truyền lại qua từng chặng (research_notes vào prompt
Analyst, rồi cả hai vào prompt Writer). Với task đơn giản, đó là trả 3.4x cho cùng một kết quả.

**d. Khi chưa đo được — bài học phương pháp từ chính lab này.** Chạy baseline hai lần trên
cùng một query cho **12.24s** và **19.31s**, lệch 58%. Với n=1, câu "multi-agent chậm hơn
1.45x" không đủ tin cậy để kết luận. Nguyên tắc: **single-agent là mặc định, multi-agent phải
tự chứng minh bằng số.**

**e. Khi lỗi cộng dồn thay vì được sửa.** 4 agent tuần tự là 4 điểm hỏng. Researcher hiểu sai
đề → Analyst phân tích sai một cách chặt chẽ → Writer viết sai một cách trôi chảy *có kèm
citation*. Sai lầm sớm được khuếch đại và khoác thêm vẻ đáng tin. Baseline chỉ có 1 điểm hỏng,
và output của nó *trông* kém tin cậy hơn — đôi khi đó lại là điều tốt.

> **Hạn chế thật trong implementation này:** supervisor chỉ đi tới, **không bao giờ quay lại**.
> Không có nhánh nào bắt Researcher tìm thêm khi Analyst phát hiện bằng chứng yếu. Nghĩa là hệ
> thống đã gánh đủ điểm hỏng của multi-agent nhưng **chưa có** cơ chế tự sửa vốn là lý do chính
> để chịu đựng sự phức tạp đó.

#### Quy tắc quyết định rút gọn

```text
Các bước có cần TOOL / năng lực khác nhau không?
├─ Không → single-agent. Dừng.
└─ Có → Có mục tiêu nào xung đột trong 1 prompt không?
    ├─ Không → single-agent + tool calling. Thường là đủ.
    └─ Có → Đã đo được multi-agent thắng chưa (n >= 3)?
        ├─ Chưa → đo trước, đừng xây trước.
        └─ Rồi → multi-agent, kèm guardrail 2 tầng.
```

**Một câu:** multi-agent trả bằng *latency và cost* để mua *năng lực và khả năng kiểm tra*.
Nếu task không cần hai thứ mua được đó, ta chỉ đang trả tiền.
