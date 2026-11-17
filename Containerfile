FROM registry.access.redhat.com/ubi9/python-312
RUN pip install flask psycopg2-binary redis
COPY --chown=1001:0 app.py .
EXPOSE 5000
CMD ["python", "app.py"]
