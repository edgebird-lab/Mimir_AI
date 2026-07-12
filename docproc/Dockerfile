# docproc: isolated document → text extractor + markdown→docx exporter. No secrets, no host access.
FROM python:3.12-slim

# pandoc: render the agent's markdown (exams/summaries/theses) into real Word + PDF documents.
# PDF uses weasyprint (HTML/CSS, NO LaTeX) so the image stays comparatively lean. Runs isolated.
RUN apt-get update && apt-get install -y --no-install-recommends \
      pandoc libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 \
      libgdk-pixbuf-2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Pure-Python / glibc-friendly parsers (no native build headaches). pdfminer.six for PDF page text,
# python-docx for Word, python-pptx for PowerPoint.
RUN pip install --no-cache-dir \
      "pdfminer.six>=20231228" \
      "python-docx>=1.1.0" \
      "python-pptx>=0.6.23" \
      "weasyprint>=60" \
      "starlette>=0.37" \
      "uvicorn>=0.30" \
      "matplotlib>=3.8" \
      "pandocfilters>=1.5"

RUN useradd -u 10001 -m docproc
COPY csl/ /app/csl/
COPY docproc/server.py /app/server.py
COPY docproc/convert.py /app/convert.py
COPY docproc/mathfilter.py /app/mathfilter.py
WORKDIR /app
USER 10001:10001
EXPOSE 8091
ENTRYPOINT ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8091", "--log-level", "warning"]
