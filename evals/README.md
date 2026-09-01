# Avaliações de extração

O runner compara tipo e campos normalizados, calcula confiança média, taxa de processamento automático, falso aceite e recall da fila de revisão.

As amostras contêm PII e **não devem ser versionadas no Git**. Monte o diretório seguro em `evals/samples` ou ajuste os caminhos do manifesto.

```powershell
& ".\.venv\Scripts\python.exe" -m evals.runner ".\evals\datasets\golden-v1.json" --persist --output ".\data\eval-report.json"
```

Cada relatório identifica dataset, Strategy, modelo e prompt. Use o mesmo dataset golden para comparar versões antes de promover modelo ou prompt.

