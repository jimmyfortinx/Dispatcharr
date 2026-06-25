import { notifications } from '@mantine/notifications';

export function showNotification(notificationObject) {
  return notifications.show(notificationObject);
}

export function updateNotification(notificationIdOrObject, notificationObject) {
  if (
    notificationIdOrObject &&
    typeof notificationIdOrObject === 'object' &&
    !Array.isArray(notificationIdOrObject)
  ) {
    return notifications.update(notificationIdOrObject);
  }

  return notifications.update({
    id: notificationIdOrObject,
    ...(notificationObject || {}),
  });
}
