"""Descubrimiento en AWS (H-009e).

ec2:DescribeInstances con el filtro de etiqueta zenit:rol=nodo, paginado. El
modelo de identidad es por region: el mismo id de instancia solo tiene
sentido junto a su cuenta y su region, y cada region se consulta aparte.

Credencial: lo correcto es un rol o usuario de IAM con AmazonEC2ReadOnlyAccess.
La cuenta de AWS Academy (Learner Lab) no permite crear roles ni usuarios, asi
que se usan las credenciales temporales de la sesion, que NO son de solo
lectura y vencen cada cuatro horas. Es una limitacion declarada en
docs/proveedores.md, no una decision: el codigo solo llama a Describe*.

Se listan tambien las instancias detenidas: el laboratorio de AWS las apaga
al cerrar la sesion y eso no es retirarlas. Las terminadas no se listan, y
tras tres ejecuciones el activo queda retirado.
"""
from typing import Optional

from zenit.catalogo import Recurso

ESTADOS_VIGENTES = ["pending", "running", "stopping", "stopped"]


class AWS:
    nombre = "aws"

    def __init__(self, region: str, etiqueta: str = "zenit:rol", valor: str = "nodo",
                 entorno: Optional[str] = "lab", cliente=None):
        self._region = region
        self._etiqueta = etiqueta
        self._valor = valor
        self._entorno = entorno
        self._cliente = cliente

    def listar(self) -> list[Recurso]:
        ec2 = self._cliente
        if ec2 is None:
            import boto3   # se crea en cada pasada: relee las credenciales de la sesion

            ec2 = boto3.client("ec2", region_name=self._region)
        salida = []
        paginas = ec2.get_paginator("describe_instances").paginate(Filters=[
            {"Name": f"tag:{self._etiqueta}", "Values": [self._valor]},
            {"Name": "instance-state-name", "Values": ESTADOS_VIGENTES},
        ])
        for pagina in paginas:
            for reserva in pagina.get("Reservations", []):
                for i in reserva.get("Instances", []):
                    etiquetas = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                    salida.append(Recurso(
                        proveedor=self.nombre,
                        id_nativo=f"instancia/{i['InstanceId']}",
                        nombre=etiquetas.get("Name") or i["InstanceId"],
                        region=self._region,
                        entorno=self._entorno,
                        atributos={"estado": i.get("State", {}).get("Name"), "tipo": i.get("InstanceType"),
                                   "zona": i.get("Placement", {}).get("AvailabilityZone")},
                    ))
        return salida
