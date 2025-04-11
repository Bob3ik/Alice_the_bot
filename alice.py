import json
import requests
from flask import Flask, request

app = Flask(__name__)

username = ''
session_state = {}


def get_username(user_id, req, res):
    global username  # Получаем доступ к глобальной переменной

    # Извлекаем текст пользователя из запроса
    user_input = req['request'].get('original_utterance', '').strip()

    if user_input:
        username = user_input  # Сохраняем в глобальную переменную
        res['response']['text'] = f"Юзернейм {username} успешно сохранен!"

        # Сбрасываем состояние сессии
        session_state[user_id]['state'] = None
    else:
        res['response']['text'] = "Пожалуйста, введите ваш юзернейм еще раз."


@app.route('/post', methods=['POST'])
def get_alice_request():
    response = {
        'session': request.json['session'],
        'version': request.json['version'],
        'response': {
            'end_session': False
        }
    }

    handle_dialog(request.json, response)
    return json.dumps(response)


def handle_dialog(req, res):
    user_id = req['session']['user_id']

    if req['session']['new']:
        # Начало новой сессии
        res['response']['text'] = 'Здравствуйте! Введите свой юзернейм в телеграмме'
        session_state[user_id] = {'state': 1}
        return

    # Обрабатываем текущее состояние пользователя
    current_state = session_state.get(user_id, {}).get('state')
    if current_state in states:
        states[current_state](user_id, req, res)
    else:
        res['response']['text'] = 'Чем еще могу помочь?'


states = {
    1: get_username
}

if __name__ == '__main__':
    app.run()
