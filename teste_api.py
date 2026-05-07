import requests
import urllib3

urllib3.disable_warnings()

CHAVE = '35299a335ede1150a49ce656e433883b'  # substitua pela sua chave atual

r = requests.get(
    "https://api.portaldatransparencia.gov.br/api-de-dados/orgaos",
    headers={"chave-api-dados": CHAVE, "Accept": "application/json"},
    verify=False
)

print("Status:", r.status_code)
print()
print("Headers da resposta:")
for k, v in r.headers.items():
    print(f"  {k}: {v}")
print()
print("Corpo da resposta:")
print(r.text)