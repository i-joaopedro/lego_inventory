"""
Script para criar o usuário administrador inicial.

"""
import os
import getpass
from app import app, db, Usuario


def criar_admin():
    with app.app_context():
        db.create_all()

        username = input("Username do admin [admin]: ").strip() or 'admin'

        if Usuario.query.filter_by(username=username).first():
            print(f"⚠️  Usuário '{username}' já existe.")
            return

        senha = getpass.getpass("Senha (mín. 6 chars): ")
        if len(senha) < 6:
            print("❌ Senha muito curta. Abortando.")
            return

        confirmacao = getpass.getpass("Confirmar senha: ")
        if senha != confirmacao:
            print("❌ Senhas não coincidem. Abortando.")
            return

        admin = Usuario(username=username, role='admin', escola='Todas')
        admin.set_password(senha)
        db.session.add(admin)
        db.session.commit()
        print(f"✅ Administrador '{username}' criado com sucesso!")


if __name__ == '__main__':
    criar_admin()
